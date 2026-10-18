# M0-13 子任务清单：内网/离线部署（Linux VM + systemd）——⏸ 暂缓

> **状态：暂缓，不纳入 M0。** 当前只要求本地可启动；本文件保留为后续内网交付参考。

> 父任务：[tasks-m0.md](./tasks-m0.md) 的 **M0-13**
> 目标：同一套代码支持 **SaaS（Vercel + 云）** 与 **内网/离线（Linux VM/裸机 + systemd + Nginx）** 两种形态；内网在禁止外网 egress 的环境下可完整启动。
> 本轮内网约束（已确认）：
>
> - **不允许 Docker Compose、也不允许 Kubernetes** → 采用 **Linux VM/裸机 + systemd 原生进程 + Nginx**；
> - **不支持 HTTPS** → Nginx 走 HTTP，Cookie 关闭 `Secure`，靠网络隔离 + SameSite + CSRF + 短会话；
> - **无内网 OIDC/LDAP** → 先用 account-service 邮箱密码；
> - 内网模型**支持 function calling**；
> - 有内网 **npm/PyPI/镜像仓库**（原生部署下主要用于 Python/Node 依赖与发布包）；
> - **SQLite 可接受**（单实例/单机）。
>   周期建议：**5 个工作日 + 2 天缓冲**；规模：M。
>   形态假设：**单台 Linux VM（systemd 可用）**；多机拆分作为后续 I3，不在本期验收。

---

## 0. 验收档位（Profiles）

| 档位        | 数据/存储         | 模型                                          | 必测            | 说明                                 |
| ----------- | ----------------- | --------------------------------------------- | --------------- | ------------------------------------ |
| I1 最小内网 | SQLite + 本地目录 | `LLM_ENABLED=false` 桩                        | ✅ 必须         | 无模型也能启动；验证「能不能跑起来」 |
| I2 内网模型 | SQLite + 本地目录 | 内网 OpenAI 兼容端点（支持 function calling） | ✅ 有环境时必测 | 验证真实对话/工具调用                |
| I3 后续多机 | 内网 PG + 多 VM   | 同 I2                                         | ⚪ 后续         | 本期不作为 M0-13 验收项              |

**统一验收底线（I1）**

- [ ] 在一台无外网（或仅内网模型/依赖仓库）的 Linux VM 上，按安装脚本部署后 `systemctl status` 全绿；
- [ ] 登录（邮箱密码）→ 新建会话 → 流式（桩）→ 上传附件 → 产物预览/下载 → 知识库检索可用；
- [ ] 日志（journald/文件）无外网请求失败；Sentry/社交 OAuth 关闭；
- [ ] `systemctl restart` / 重启 VM 后 SQLite 数据仍在；升级/回滚路径可用；
- [ ] Nginx 走 HTTP；Cookie `Secure=false`，CSRF 生效。

---

## 1. 子任务总览

| ID       | 子任务                                                        | 规模 | 依赖     | 交付物                                 |
| -------- | ------------------------------------------------------------- | ---- | -------- | -------------------------------------- |
| M0-13-01 | BFF 核心 handler 抽取 + Node 进程入口（Vercel/Native 双形态） | M    | —        | `packages/bff-core`、Node 入口         |
| M0-13-02 | Nginx 静态站 + SPA rewrite + /api 反代 + SSE/上传             | S    | 01       | `deploy/native/nginx/`                 |
| M0-13-03 | llm-gateway 内网模型配置 + 桩模式 + function calling 连通测试 | S    | —        | 配置项 + mock 测试                     |
| M0-13-04 | systemd 单元 + 安装/升级脚本（六服务 + BFF + Nginx）          | M    | 01/02/03 | `deploy/native/systemd/`、`install.sh` |
| M0-13-05 | 环境文件/密钥/启动校验（EnvironmentFile）                     | S    | 04       | `/etc/beauty/*.env` 模板               |
| M0-13-06 | SQLite 数据目录/迁移/备份恢复（单机）                         | M    | 04       | 迁移脚本、备份 timer、恢复脚本         |
| M0-13-07 | 内网鉴权（邮箱密码 + HTTP Cookie + CSRF）                     | M    | 01/04    | 登录闭环、配置                         |
| M0-13-08 | 内网构建与发布包（内网 PyPI/npm + 静态产物）                  | M    | 04       | `releases/` 发布包、安装/升级脚本      |
| M0-13-09 | 断网验收自动化（防火墙 egress deny + 冒烟）                   | S    | 04/06/07 | `deploy/native/verify.sh`              |
| M0-13-10 | 内网部署手册/升级回滚/排障/已知限制                           | S    | 01–09    | `docs/intranet-deployment.md`          |

总计 10 项（M=5 / S=5）；排期见第 3 节。

---

## 2. 子任务详情

### M0-13-01 BFF 核心 handler 抽取 + Node 进程入口

**目标**：BFF 路由与部署平台解耦；Vercel Functions 与 **systemd 管理的 Node 进程** 共用一套 handler。

**范围**

- 新建 `packages/bff-core`（建议 Hono）：auth/cookie、runtime 代理 allowlist、错误契约、限流、下载、stream token、health；
- Vercel 入口：`apps/web/api/[...path].ts` 调 `handle(app)`；
- Native 入口：`deploy/native/bff/server.mjs`（`@hono/node-server`），由 systemd 拉起；`BFF_RUNTIME=vercel|node`；
- 核心包禁止 import Vercel 专有 API；cookie/stream/下载两种 adapter 行为一致；
- **HTTP 内网**：Cookie `Secure=false` 可配置；SameSite=Lax + CSRF double-submit；
- 进程：监听 `127.0.0.1:8787`（只给 Nginx 访问），优雅退出（SIGTERM），启动失败退出码非 0。

**验收**

- [ ] 同一组契约测试在 Vercel 与 Node 两种 adapter 下通过；
- [ ] Cookie 设置/清除、SSE 流式、文件流式下载、`/api/health` 均可用；
- [ ] HTTP 下可登录（`BFF_COOKIE_SECURE=false`）；HTTPS 时保持 true；
- [ ] systemd 停止/重启时进程优雅退出、无残留连接。

**测试**：adapter 契约；SSE/下载 mock；cookie 属性；CSRF；优雅退出。

**依赖/风险**：无。Node 版本需与构建机一致（建议 LTS），在文档中锁定。

---

### M0-13-02 Nginx 静态站 + SPA rewrite + /api 反代 + SSE/上传

**目标**：内网用系统级 Nginx 托管前端产物并反代 BFF。

**范围**

- 安装包/系统服务：系统 Nginx（或内网提供的 Nginx 包）；
- 站点配置 `deploy/native/nginx/beauty.conf`：
  - `location / { try_files $uri /index.html; }`（SPA 深链）；
  - `location /api/ { proxy_pass http://127.0.0.1:8787; proxy_buffering off; proxy_read_timeout 3600s; }`（SSE）；
  - `client_max_body_size 12m`；gzip；静态缓存；安全头；
  - **仅 HTTP**（无 TLS）；
- 静态根目录 `/opt/beauty/current/web`；可选官网静态站（host/path 区分）；
- 限制：只监听内网地址/端口；管理面 IP allowlist 由防火墙/上游负责。

**验收**

- [ ] 刷新 `/runs`、`/changes` 等深链不 404；
- [ ] SSE 不被缓冲（首字节及时、长连接不中断）；
- [ ] 10MB 附件上传成功，11MB 被拒绝；
- [ ] 静态资源无外链；`/api/health` 正常；
- [ ] `nginx -t` 通过；reload 不中断服务。

**测试**：curl 深链/上传/SSE；响应头；浏览器联调。

**依赖**：M0-13-01。

---

### M0-13-03 llm-gateway 内网模型配置 + 桩模式

**目标**：模型可指向内网 OpenAI 兼容端点；无模型时可启动（桩）；支持 function calling。

**范围**

- 配置新增：`api_base`、`api_key`、`default_model`（env：`LLM_GATEWAY__API_BASE`/`API_KEY`/`DEFAULT_MODEL`）；
- `complete/stream/embedding` 调 litellm 时透传 `api_base`/`api_key`；
- 桩模式：orchestrator `LLM_ENABLED=false` 时 plan/execute 走确定性桩（现有行为）；
- **function calling**：验证内网模型返回 tool_calls 时，网关归一化 + orchestrator 工具执行链路可用；不支持时明确降级/错误；
- 错误映射：未配置/不可达/超时 → 明确 502 与日志；不静默回退外网；
- mock OpenAI server 验证路径/鉴权/流式/tool_calls。

**验收**

- [ ] 不配模型、`LLM_ENABLED=false`：可启动，会话/流程可跑（桩）；
- [ ] 配 `api_base` + `api_key` + `openai/<model>`：真实补全/流式成功；
- [ ] function calling 可用（mock 或内网模型）；不可用时错误清晰；
- [ ] 模型不可达时错误清晰、无外网兜底；日志不打印 api_key/正文。

**测试**：mock server；litellm 参数断言；tool_calls 归一化；错误路径。

**依赖**：无；与 M0-13-04 联调。

---

### M0-13-04 systemd 单元 + 安装/升级脚本

**目标**：一条安装脚本在单台 Linux VM 上拉起完整内网栈；systemd 管进程、日志、重启。

**范围**

- `deploy/native/systemd/`：`beauty-account.service`、`beauty-runtime.service`、`beauty-llm-gateway.service`、`beauty-connector.service`、`beauty-knowledge.service`、`beauty-workspace.service`、`beauty-bff.service`（Nginx 用系统服务）；
- 进程模型：Python 用 `uvicorn`（`venv/bin/uvicorn ...`）；BFF 用 `node`；统一 `User=beauty`、`WorkingDirectory=/opt/beauty/current/...`；
- `Restart=on-failure`、`RestartSec`、`TimeoutStopSec`、`EnvironmentFile`、`LimitNOFILE`、`NoNewPrivileges`、`ProtectSystem`/`ProtectHome`（按需）；
- 依赖顺序：account → 其他服务 → BFF；Nginx `After=beauty-bff.service`；
- 迁移：`beauty-migrate.service`（oneshot）或安装脚本先执行 `alembic upgrade head`；
- 安装脚本 `deploy/native/install.sh`：建用户/目录、解压 release、软链 `current`、装依赖、写 env、systemctl daemon-reload/enable/start；升级脚本切换软链 + 迁移 + 重启；
- 日志：journald（`journalctl -u beauty-*`）；必要时文件轮转；
- 单机部署；多机拆分留 I3（需服务地址/共享存储方案）。

**验收**

- [ ] 干净 VM 执行安装脚本后 `systemctl status beauty-*` 全绿；
- [ ] 服务异常退出自动重启；重启 VM 后全部自启；
- [ ] 升级脚本切换 release 后服务可用；回滚脚本恢复上一 release；
- [ ] 非 root 运行；端口仅监听 127.0.0.1（BFF/服务）或内网地址（Nginx）。

**测试**：安装/升级/回滚演练；kill 进程自动重启；VM 重启；权限检查。

**依赖**：01/02/03。

**开放问题**：若内网连 systemd 也不允许，需要改为 `supervisor`/手工脚本 + 进程守护；请确认。

---

### M0-13-05 环境文件/密钥/启动校验

**目标**：配置清晰、可复制、启动即校验；密钥不落仓库。

**范围**

- `/etc/beauty/*.env`（`EnvironmentFile`）：六服务 + BFF；模板 `deploy/native/env/*.env.example`；
- Secret：JWT 签名密钥、`INTERNAL_AUTH_SECRET`、`BFF_SESSION_SECRET`、BYOK 加密密钥；生成脚本写入 `/etc/beauty/secrets/`，权限 600/640（`beauty` 用户）；
- **HTTP 内网**默认：`BFF_COOKIE_SECURE=false`、`SENTRY_ENABLED=false`、`SOCIAL_LOGIN_ENABLED=false`、`REDIS_URL=` 空；
- 启动校验：必要 URL/密钥缺失 fail-fast；禁止生产默认密钥；`/api/health` 汇总依赖状态；
- 配置矩阵文档：变量 → 服务 → 默认 → 内网建议值。

**验收**

- [ ] 新 VM 按模板生成 env/密钥后可直接启动；
- [ ] 缺少关键变量时启动失败并给出明确提示；
- [ ] env/密钥文件权限正确，不写入发布包/日志；
- [ ] 配置矩阵覆盖 BFF/六服务/模型/存储/可选依赖。

**测试**：配置校验单测；空值/错误值启动测试；权限检查。

**依赖**：04。

---

### M0-13-06 SQLite 数据目录/迁移/备份恢复（单机）

**目标**：单机数据可靠、可升级、可备份。

**范围**

- 目录：`/var/lib/beauty/<service>/*.db`、`/var/lib/beauty/artifacts`、`/etc/beauty/secrets`；属主 `beauty:beauty`；
- 迁移：安装/升级脚本执行 `alembic upgrade head`；空库初始化；失败回滚 release；
- 备份：`deploy/native/backup.sh` + systemd timer（或 cron）：先 WAL checkpoint / `sqlite3 .backup`，再打包 DB + artifacts 到 `/var/backups/beauty/<date>/` 或内网 NAS；
- 恢复脚本：停服务 → 解包 → 校验 → 启动；演练记录；
- 升级：备份 → 新 release 迁移 → 验证；回滚：旧 release + 备份恢复（迁移需向前兼容）。

**验收**

- [ ] 重启 VM / 进程后数据不丢；
- [ ] 备份→恢复后数据一致（含产物）；
- [ ] 空库迁移成功；升级迁移成功；回滚演练通过；
- [ ] 写入/备份并发时数据一致（WAL/checkpoint 策略）。

**测试**：备份恢复；迁移 up/down；进程重启；并发写。

**依赖**：04。

**风险**：单机 SQLite 无 HA；多机/高并发需 PG（I3）。

---

### M0-13-07 内网鉴权（邮箱密码 + HTTP Cookie + CSRF）

**目标**：无外部 IdP 时可登录；HTTP 内网下安全边界明确。

**范围**

- 默认：account-service 邮箱密码注册/登录/刷新；BFF httpOnly Cookie；
- **HTTP 内网**：`BFF_COOKIE_SECURE=false`、`SameSite=Lax`、短会话 TTL + 刷新；CSRF double-submit；登录限流（进程内/可选本地 Redis）；
- 社交 OAuth 关闭（`SOCIAL_LOGIN_ENABLED=false`）；不接 OIDC/LDAP；
- 安全补偿（无 HTTPS）：防火墙仅内网可达、Nginx 监听内网地址、管理面 IP allowlist、日志/截图不泄 token、Referrer-Policy、CSP；
- 文档明确「HTTP 下凭证与 Cookie 明文传输」风险，建议后续上内部 TLS（Nginx 自签/内部 CA）。

**验收**

- [ ] 内网 HTTP 下可登录/刷新/登出；Cookie 生效；
- [ ] 未登录访问受保护页 401/跳登录；CSRF 拦截生效；
- [ ] 无外网 IdP 不影响本地登录；
- [ ] 安全补偿项在部署文档中逐条落地。

**测试**：登录 e2e；Cookie 属性；CSRF；限流；越权。

**依赖**：01/04。

---

### M0-13-08 内网构建与发布包

**目标**：在内网完成构建、打包与部署，不依赖公网与容器镜像。

**范围**

- Python：`uv sync --frozen` 使用内网 PyPI 镜像；或构建 wheelhouse 后 `uv pip install --find-links`；锁定 `uv.lock`；
- Node/BFF：内网 npm 镜像；BFF 用 esbuild/tsup 打成单文件（减少 `node_modules`）；锁定 `package-lock.json`；
- Web：`vite build` 产物；`apps/site` 静态产物；Nginx 静态根；
- 发布包：`releases/<version>/`（服务代码/venv 或 wheels + dist + env 模板 + systemd/nginx 配置 + 迁移脚本 + 校验和）；
- 安装/升级脚本：解压 → 软链 `current` → 迁移 → 重启；回滚切换软链；
- 可选：内网制品库（Nexus/Artifactory）存放发布包/依赖。

**验收**

- [ ] 内网构建机可产出完整发布包；
- [ ] 目标 VM 无公网也能安装/升级；
- [ ] 版本、校验和、回滚版本可追溯；
- [ ] 发布包不含密钥/真实数据。

**测试**：内网构建演练；无网安装；升级/回滚。

**依赖**：04/05/06。

---

### M0-13-09 断网验收自动化

**目标**：把「内网可启动」变成可重复的测试。

**范围**

- `deploy/native/verify.sh`：
  1. 检查/应用防火墙规则（`nftables`/`iptables`）默认拒绝出站，仅放行 DNS/内网模型/内网邮件/NAS；
  2. `systemctl restart` 全栈并等待健康；
  3. 冒烟：登录 → 新建会话 → 流式（桩）→ 上传 → 产物 → 检索；
  4. 断言 journald/日志无外网请求失败/DNS 错误；
  5. 输出 PASS/FAIL + 诊断（`systemctl status`、`journalctl`、`ss -lntp`）；
- 覆盖 I1 必须；I2 有内网模型时追加 function calling 用例；
- 可选：在隔离网络命名空间/单独 VM 执行；结果归档。

**验收**

- [ ] 断网 VM 上脚本返回 0；任一冒烟失败非 0 并输出诊断；
- [ ] 日志断言能捕获误配外网依赖（Sentry/fonts 等）；
- [ ] 报告可作为 M0 验收附件；
- [ ] 故意断一项（如停 BFF）脚本能失败。

**测试**：脚本自测；防火墙规则生效验证。

**依赖**：04/06/07。

---

### M0-13-10 内网部署手册/升级回滚/排障/已知限制

**目标**：交付/运维可独立完成部署与升级。

**范围**

- `docs/intranet-deployment.md`：架构图、前置条件（Linux 版本/systemd/Nginx/Python/Node/内网依赖源）、安装、配置、验证、升级、回滚、备份、排障；
- 已知限制：无 HTTPS、单机 SQLite、无 HA、无代码执行沙箱、附件 10MB、无对象存储、无外部支付/邮件、内网模型能力差异；
- 安全清单：密钥与文件权限、HTTP 风险、防火墙、日志脱敏、Cookie 配置、管理面限制；
- FAQ：服务起不来、迁移失败、SSE 不流、登录失败、模型不可达、磁盘满、端口占用。

**验收**

- [ ] 新同事按文档 ≤30 分钟完成安装（测试 VM）；
- [ ] 回滚/备份步骤可执行；
- [ ] 已知限制与安全注意事项完整。

**测试**：文档走查/演练。

**依赖**：01–09。

---

## 3. 排期建议（5 天 + 2 天缓冲）

| 天    | 主任务                                        | 并行                                 |
| ----- | --------------------------------------------- | ------------------------------------ |
| D1    | M0-13-01 BFF 核心/Node 入口                   | M0-13-02 Nginx、M0-13-03 LLM 配置    |
| D2    | M0-13-04 systemd/安装脚本                     | M0-13-05 env/密钥、M0-13-07 鉴权设计 |
| D3    | M0-13-06 SQLite/迁移/备份                     | M0-13-07 鉴权联调                    |
| D4    | M0-13-08 发布包                               | M0-13-09 验收脚本                    |
| D5    | 联调修复 + M0-13-10 文档                      | 缓冲                                 |
| D6–D7 | 断网验收、I2 模型/function calling 验证、修复 | 缓冲                                 |

**关键路径**：01 → 04 → 09；02/03 可与 01 并行；06/07/08 在 04 之后并行。
**最早可开始**：01、02、03。

---

## 4. 交付物清单（建议路径）

```
packages/bff-core/                     # Hono 核心 handler + 契约测试
deploy/native/
  systemd/                             # beauty-*.service
  nginx/beauty.conf                    # 静态 + /api 反代 + SSE
  bff/server.mjs                       # Node 进程入口
  env/*.env.example                    # 六服务 + BFF
  install.sh / upgrade.sh / rollback.sh
  backup.sh / restore.sh               # SQLite + artifacts
  verify.sh                            # 断网验收
docs/intranet-deployment.md
```

---

## 5. 验收矩阵

| 检查项                        | I1（SQLite+桩） | I2（内网模型） | 说明                 |
| ----------------------------- | --------------- | -------------- | -------------------- |
| 服务 systemd active           | ✅              | ✅             | 六服务 + BFF + Nginx |
| 登录/刷新/登出（HTTP Cookie） | ✅              | ✅             | Secure=false         |
| 会话列表/新建                 | ✅              | ✅             |                      |
| 流式回复                      | ✅（桩）        | ✅（真实）     | SSE 不缓冲           |
| function calling              | —               | ✅             | 内网模型支持         |
| 上传/产物/下载                | ✅              | ✅             | 本地目录             |
| 知识库检索                    | ✅（本地嵌入）  | ✅             |                      |
| 无外网请求                    | ✅              | ✅             | 防火墙 + 日志断言    |
| 重启 VM 数据保留              | ✅              | ✅             | 本地目录             |
| 备份/恢复                     | ✅              | ✅             | timer + 脚本         |
| 迁移/升级/回滚                | ✅              | ✅             | release 软链         |
| 多机一致性                    | —               | —              | I3 后续（PG）        |

---

## 6. 风险与开放问题

| 风险/问题                       | 处理                                                                                            |
| ------------------------------- | ----------------------------------------------------------------------------------------------- |
| 不允许 Compose/K8s              | Linux VM + systemd + Nginx；若 systemd 也不允许，需改为 supervisor/手工守护，另立方案           |
| 无 HTTPS                        | Cookie Secure=false；防火墙/内网隔离 + CSRF + 短会话 + IP allowlist；文档警告；后续建议内部 TLS |
| 单机 SQLite                     | 无 HA；单机部署；扩容前切 PG（I3）                                                              |
| 单机单点                        | systemd 自动重启；备份/快速恢复；多机拆分后续                                                   |
| 无 Redis                        | 进程内限流/锁；关键状态在 SQLite；单机可接受                                                    |
| 内网模型 tools/function calling | 已支持；用 mock 固定回归；不支持时降级                                                          |
| 内网构建依赖                    | 内网 PyPI/npm；锁文件；发布包含 wheels/dist/校验和                                              |
| 防火墙误配                      | 白名单 DNS/模型/邮件/NAS；验收脚本覆盖                                                          |
| 密钥管理                        | /etc/beauty + 600；不落发布包/日志；后续接 Vault/KMS                                            |
| 无 Sentry                       | journald + 结构化日志 + 验收脚本；内网可接自建日志平台（后置）                                  |

**待确认**

1. 内网 Linux 发行版/版本、是否统一 systemd、Python/Node 版本；
2. 是否允许安装系统 Nginx（还是已有反向代理）；
3. 内网 PyPI/npm 源地址与认证方式；
4. 内网模型 endpoint/模型名（已确认支持 function calling）；
5. 防火墙工具（nftables/iptables）与放行清单；
6. 单机 SQLite 是否接受（已确认可接受）；是否需要多机拆分；
7. 备份落点（本地盘/NAS/内网对象存储）；
8. **若连 systemd 也不允许，请说明允许的进程托管方式**（supervisor/Windows 服务/特定平台）。

---

## 7. M0-13 完成定义（DoD）

- [ ] 10 个子任务完成并通过各自验收；
- [ ] I1 档位断网验收 PASS（必须）；
- [ ] I2 档位在内网模型可用时 PASS（含 function calling）；
- [ ] 安装/升级/回滚脚本在干净 VM 演练通过；
- [ ] SQLite + artifacts 备份/恢复演练通过；
- [ ] 内网部署手册/回滚/备份/排障文档完成；
- [ ] 无外网请求失败日志；无 Secret 泄漏；
- [ ] 已知限制与待确认项登记完毕。
