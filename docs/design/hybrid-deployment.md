# 混合部署落地：Vercel + 腾讯云轻量（首尔）+ Neon

> 状态：落地定稿（2026-11-11）。本文是**当前部署主线**：Web 静态层放 Vercel，8 个 Python 服务全部跑在腾讯云轻量应用服务器（首尔）单机 Docker Compose 上，数据库用 Neon，沙箱外置。
> 关联文档：[serverless-deployment.md](./serverless-deployment.md)（平台横向对比与 Fly / Cloud Run 细节，决策背景）、[architecture.md](../architecture.md)（§10 部署与迁移、ADR-012）、[deploy/tencent/README.md](../../deploy/tencent/README.md)（运维手册）。

## 1. 结论与范围

| 层         | 选型                                             | 说明                                                                               |
| ---------- | ------------------------------------------------ | ---------------------------------------------------------------------------------- |
| Web / 官网 | Vercel                                           | 现状不动；`/api/:path*` rewrite 到公网 API 网关                                    |
| 计算       | 腾讯云轻量（首尔）2C4G 30M，Docker Compose       | 8 个 Python 服务 + Caddy 全量；升配 4C8G                                           |
| 入口       | Caddy 自动 HTTPS，仅暴露 `api-cvm.beautymoon.cn` | 只反代 api-gateway:8007，其余服务仅内网                                            |
| 数据库     | Neon（新加坡），6 个库                           | 一服务一库；首尔到新加坡 RTT ≈ 70–90ms                                             |
| 沙箱       | 外置 E2B 控制面（官方云 / 阿里云极速版，已落地） | 轻量无 `/dev/kvm`；`SANDBOX_PROVIDER=e2b` + `E2B_API_KEY`；无 Key 时 `unavailable` |
| 交付物     | 过渡期本地卷，后续 COS                           | `ARTIFACT_STORE=filesystem`；COS 走 S3 兼容端点                                    |
| 密钥       | 主机 `.env`（600）+ 卷内 PEM                     | 后续可换 Secret Manager / KMS                                                      |

**为什么不是「尽量都放 Vercel」**：api-gateway 要代理 SSE 长流（Vercel 函数时长 Hobby 300s / Pro 800s），agent-orchestrator 跑分钟级 run 与 5 个常驻循环，workspace/connector 也各带常驻循环，这些都不是函数模型能承载的；把 8 个服务放同一台机器还顺带消灭了跨云鉴权与内网延迟问题。详见 serverless-deployment.md §3.2/§3.3。

**为什么不是 Fly / Cloud Run**：Fly 需要重写沙箱 provider 为 Fly Machines 且多区域/合规弱；Cloud Run 需要拆 worker pool、换 GCS/Cloud SQL/Secret Manager，改造量 13–19 人日。轻量 + Compose 的改造面最小，且成本低一个量级。

## 2. 拓扑

```text
浏览器
  │  https://app.beautymoon.cn        （Vercel：静态 SPA + 深链 rewrite）
  │      /api/:path*  ──rewrite──►  https://api-cvm.beautymoon.cn
  ▼
https://api-cvm.beautymoon.cn             （DNS A → 轻量公网 IP）
  │  Caddy :443  （自动证书；flush_interval -1，SSE 不缓冲）
  ▼
┌─────────────── 轻量（docker compose 网络 beauty）───────────────┐
│ api-gateway:8007  ← 唯一对外（127.0.0.1:8007）                    │
│   ├─► agent-orchestrator:8002    run 编排 / SSE / 常驻循环        │
│   ├─► account-service:8001       认证 / JWKS / 令牌              │
│   ├─► workspace-service:8005     交付物 / 沙箱生命周期            │
│   ├─► knowledge-service:8006     文档库                          │
│   ├─► connector-service:8004     OAuth / 凭证 / 24h 刷新巡检      │
│   ├─► tool-service:8008          工具执行 / 审计                  │
│   └─► llm-gateway:8003           唯一模型出口                     │
└──────────────────────────────────────────────────────────────────┘
   │                    │                        │
   ▼                    ▼                        ▼
 Neon（新加坡）      Modal Sandboxes          COS（后续）
 6 库 pooler         argv + stdin
```

## 3. 硬约束逐项对照

### 3.1 SSE 长流

- 现状：run 事件流 `GET /api/v1/runs/{id}/events/stream` 支持 `Last-Event-ID`；api-gateway 逐块代理（`services/api-gateway/api_gateway/api/proxy.py`），Caddy 用 `flush_interval -1` 保证逐事件下发。
- 风险点：**浏览器 → Vercel rewrite → 轻量** 这一跳。Vercel 的函数时长限制不适用于外部 rewrite 代理，但代理行为（缓冲/超时）需要实测。
- 兜底：前端直连 `https://api-cvm.beautymoon.cn`（Cookie 改 `SameSite=None; Secure` + 网关 CORS allowlist），或让 Caddy 直连；`Last-Event-ID` 续流保证断线可恢复。

### 3.2 长任务与常驻循环

- 分钟级 run 在 HTTP 请求内跑完 + 续流，单机 Docker 无请求时长上限，天然满足。
- 常驻循环（同机运行，无需 worker 拆分）：
  - agent-orchestrator：outbox dispatcher、jobs worker、interrupts sweeper、run_timeout sweeper、memory sweeper；
  - workspace-service：沙箱 reaper（`SANDBOX_REAPER_ENABLED`）、shell 会话清理；
  - connector-service：outbox dispatcher + 24h 刷新巡检（`refresh_patrol_interval_seconds=3600`）。
- 多实例正确性已在 912e15b 补齐（outbox `FOR UPDATE SKIP LOCKED`、jobs lease 心跳、reaper 原子 claim），单机场景更无压力。

### 3.3 鉴权

- 全部服务同机：`AUTH_ISSUER=https://api-cvm.beautymoon.cn`，account-service 签发、兄弟服务经 JWKS 验签，无需跨云信任配置。
- `ACCOUNT__SIGNING_KEY_PATH` 指向卷内 PEM；文件不存在时自动生成并 0600 落盘，重启不轮换 kid。

### 3.4 沙箱（唯一真实缺口）

- 轻量无 `/dev/kvm`，`microsandbox` 不可用；`local` 等于在宿主机跑用户代码，**禁止生产使用**。
- 落地前用 `unavailable`：工具执行返回明确错误，其余功能正常。
- P0：新增 Modal provider（`services/workspace-service/workspace/services/sandbox_provider/modal.py`），实现 `protocols.py` 契约（argv + stdin + 超时 + 输出上限），由 `factory.py` 按 `sandbox_provider=modal` 装配；凭证 `MODAL_TOKEN_ID` / `MODAL_TOKEN_SECRET`。

### 3.5 Neon

- Neon 亚太区只有新加坡与悉尼（2026-10 官方列表），首尔 → 新加坡 RTT ≈ 70–90ms；若把机器放新加坡，同区 ≈ 2ms。
- 用 pooler 端点；asyncpg + PgBouncer 事务模式需 `statement_cache_size=0`。
- 当前 `packages/database/database/session.py` 是 `NullPool`（每请求新建连接）：延迟与连接数都不理想，P0 改为 `QueuePool + pool_pre_ping`（或至少加 `connect_args`）。
- 6 个库一服务一库，迁移按 account → agent → connector → knowledge → workspace → tool 串行。

### 3.6 交付物与文件

- 过渡期 `ARTIFACT_STORE=filesystem` + 本地卷；单机可用。
- 后续切 COS（S3 兼容）：`ARTIFACT_STORE=s3` + endpoint/ak/sk；Vercel 代理有 4.5MB 请求上限，大文件走 COS 直传或直连 api 子域。
- 磁盘：60GB 盘，交付物与镜像层共用；定期 `docker system prune`。

### 3.7 密钥与配置

- 全量配置走一个 `.env`（600），compose `env_file` 注入。
- 必须同值的密钥对：`ACCOUNT__INTERNAL_SECRET` = `API_GATEWAY__INTERNAL_SECRET` = `AGENT_ORCHESTRATOR__INTERNAL_AUTH_SECRET` = `LLM_GATEWAY__INTERNAL_SECRET` = `KNOWLEDGE__INTERNAL_SECRET` = `TOOL__INTERNAL_SECRET`；`CONNECTOR__INTERNAL_SECRET` = `TOOL__CONNECTOR_INTERNAL_SECRET`。

## 4. 改造清单

### P0（上线前必须）

| #   | 改造                                                       | 位置 / 说明                                                   |
| --- | ---------------------------------------------------------- | ------------------------------------------------------------- |
| 1   | Modal SandboxProvider + 工厂装配                           | `workspace/services/sandbox_provider/modal.py`、`factory.py`  |
| 2   | Neon 连接池：`QueuePool + pool_pre_ping`，asyncpg 语句缓存 | `packages/database/database/session.py`                       |
| 3   | 生产 compose + Caddy + 脚本进仓库                          | `deploy/tencent/`（本文件同批落地）                           |
| 4   | Caddy SSE 配置验证（长 run 不缓冲、不断流）                | `deploy/tencent/Caddyfile`                                    |
| 5   | 签名 PEM 持久化 + 备份                                     | `ACCOUNT__SIGNING_KEY_PATH=/data/keys/...`，卷 `account-keys` |
| 6   | 迁移脚本（6 库串行、幂等）                                 | `deploy/tencent/scripts/migrate.sh`                           |
| 7   | 首次部署验收（登录 / SSE / 工具 / 交付物）                 | 见 §9                                                         |

### P1（上线 2 周内）

| #   | 改造                                                    |
| --- | ------------------------------------------------------- |
| 8   | 每日 `pg_dump` 到 COS + 密钥卷备份                      |
| 9   | 云监控告警（CPU/内存/磁盘/容器退出）                    |
| 10  | 发布脚本自动化（TCR 推送 → pull → 健康等待 → 失败回滚） |
| 11  | 交付物切 COS（S3 兼容）                                 |
| 12  | 大文件走直连 api 子域（绕开 Vercel 4.5MB）              |
| 13  | CI 增加 `docker compose config -q` + amd64 镜像构建     |
| 14  | 日志采集（Caddy 访问日志 + 容器 stdout → CLS 可选）     |

### P2（规模化）

- 多机 + CLB + 共享 COS/Redis；拆 api/worker 角色；
- 数据库迁移/Schema 变更流程与回滚演练；
- OTel Collector + Langfuse（compose 已有 observability profile，可单独起）。

## 5. 配置矩阵（关键项）

| 服务               | 关键配置                                                                                                                          |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------- |
| api-gateway        | `APP_ORIGIN=https://app.beautymoon.cn`、`RUNTIME_PUBLIC_URL`、`COOKIE_SECURE=true`、`INTERNAL_SECRET`、上游 URL 用 compose 服务名 |
| account-service    | `AUTH_ISSUER`、`SIGNING_KEY_PATH`、`INTERNAL_SECRET`、`OAUTH21_WEB_ORIGIN`                                                        |
| agent-orchestrator | `AUTH_ISSUER`、`INTERNAL_AUTH_SECRET`、`LLM_GATEWAY_URL`、`TOOL/KNOWLEDGE/WORKSPACE_ENABLED=true`、`OUTBOX_ENABLED=true`          |
| workspace-service  | `ARTIFACT_ROOT`、`SANDBOX_PROVIDER`、`SANDBOX_REAPER_ENABLED`                                                                     |
| connector-service  | `CREDENTIAL_SECRET`、`INTERNAL_SECRET`、`OAUTH_REDIRECT_BASE`                                                                     |
| tool-service       | `WORKSPACE_SERVICE_URL`、`CONNECTOR_BASE_URL`、`CONNECTOR_INTERNAL_SECRET`、`AUTH_ISSUER`                                         |
| knowledge-service  | `EMBEDDING_PROVIDER=local`、`LLM_GATEWAY_URL`、`INTERNAL_SECRET`                                                                  |
| llm-gateway        | `API_KEY`、`API_BASE`、`DEFAULT_MODEL`、`ACCOUNT_BASE_URL`、`INTERNAL_SECRET`                                                     |

完整样例见 `deploy/tencent/.env.example`。

## 6. 上线路径（4–6 人日）

| 天   | 内容                                                           |
| ---- | -------------------------------------------------------------- |
| D1   | 买机（首尔 2C4G 30M）、Ubuntu 24.04、Docker、swap、安全组、DNS |
| D1–2 | Neon 6 库 + pooler 连接串；连接池改造；迁移执行                |
| D2–3 | Modal provider 落地（含单测/打桩）+ 配置；镜像构建推 TCR       |
| D3   | `.env` 填全、`deploy.sh` 起服务、验收登录/SSE/交付物           |
| D4   | 灰度 `api2` → 切 `api`、观察 24h                               |
| 缓冲 | 备份/监控/发布脚本；沙箱在境外带来的时延实测                   |

## 7. 成本（月，2026-11 价目）

| 项                    | 2C4G 起步      | 4C8G 升配     |
| --------------------- | -------------- | ------------- |
| 轻量（首尔）          | ≈¥17（199/年） | ¥250          |
| Neon Launch           | ≈¥110          | ≈¥110         |
| Vercel（Hobby → Pro） | ¥0–145         | ¥0–145        |
| Modal（按量）         | ¥0–30          | ¥0–30         |
| COS                   | ¥5–20          | ¥5–20         |
| **合计**              | **≈¥130–320**  | **≈¥365–555** |

流量：30M 峰值 + 1.5TB/月；超额 0.8 元/GB。拉 2.5GB 镜像约 11 分钟。

## 8. 容量

- 2C4G：常驻约 2.8GB（8 服务 + Caddy + OS），余量 1.2GB → 2–3 并发 run，1–2 人内测。
- 4C8G：余量 5.2GB → 10–15 并发 run / 30–50 在线。
- 单机天花板：发布时 SSE 全断（客户端续流恢复）、磁盘单点、无法横向扩。

## 9. 验收清单

- [ ] `https://api-cvm.beautymoon.cn/healthz` 200，证书有效
- [ ] 注册/登录 → Cookie `Secure/HttpOnly/SameSite` 正确，刷新不掉线
- [ ] 发消息 SSE 逐字输出；>5 分钟 run 不断流；断线后 `Last-Event-ID` 补齐
- [ ] 工具执行（Modal 落地后）与 `unavailable` 错误语义正确
- [ ] 交付物上传/下载；>4.5MB 场景有明确替代路径
- [ ] account-service 重启后 JWKS 不变、旧 token 仍可验
- [ ] 6 库迁移二次执行无报错
- [ ] 机器重启后 `restart: unless-stopped` 自愈
- [ ] 24h `docker stats` 无内存爬升；磁盘 <70%

## 10. 风险与回退

| 风险                              | 缓解                                                 |
| --------------------------------- | ---------------------------------------------------- |
| Vercel rewrite 对 SSE 有缓冲/超时 | 灰度实测；兜底直连 api 子域 + `SameSite=None` + CORS |
| 首尔 → Neon 新加坡 70–90ms        | 接受（低流量），或机器改新加坡；或换同区 TencentDB   |
| Modal provider 未就绪             | 上线前完成；期间工具执行不可用但不阻塞主链路         |
| 2C4G OOM                          | swap + `mem_limit`；升 4C8G                          |
| 单机故障 / 磁盘满                 | 每日备份 + 可重建；磁盘告警；交付物外置              |
| 30M 跨境质量 / 大文件慢           | 大文件走 COS 直传；镜像本地构建推 TCR                |
| 域名/证书                         | 先 `api2` 灰度，保留旧解析 24h                       |

## 11. 备选与关系

- **新加坡替首尔**：Neon 同区，DB RTT 从 ≈80ms 降到 ≈2ms，其余不变；无韩国业务要求时更优。
- **4C8G 通用型（¥250/月）**：并发上到 10+ 时升配，编排不变。
- **Fly / Cloud Run**：见 serverless-deployment.md §7.1/§7.5；若未来要多区域或托管扩缩容再评估。
- **纯 Vercel**：只适用于无 SSE、无长任务、无沙箱的静态产品，不适用本仓库。

与旧文档的口径变化：

1. serverless-deployment.md 的「Cloud Run 首选」不再代表当前决策（保留为备选分析）；
2. 该文档 §4.6「worker 必须单实例 / 租约无续期」在 912e15b 后已过时；
3. 该文档「Vercel 不推荐」仍然成立（针对全量后端），但**静态层 + rewrite 入口**是当前方案的一部分；
4. 该文档 §7.6 的服务级 Vercel 导出配置（`services/*/vercel.json`、`services/*/.python-version`、`pyproject.toml` 的 `[tool.vercel]`、`scripts/check-vercel-config.py`）已于 2026-11-12 移除，Vercel 侧只保留 `apps/web` 与 `packages/design` 的 `vercel.json`。
