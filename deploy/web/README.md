# 部署与容器化（Web + BFF + 八个 Python 服务）

本目录是**容器部署入口**。三条默认路径：

| 场景                        | 入口                                         | 说明                                            |
| --------------------------- | -------------------------------------------- | ----------------------------------------------- |
| 本地开发（裸机，热更新）    | [docs/local-dev.md](../../docs/local-dev.md) | `npm run dev`，八服务 + BFF + Vite 都跑在宿主机 |
| 容器演示 / 自托管（本文件） | `deploy/docker-compose.yml`                  | 一条 `up` 起全栈，访问 <http://localhost:8080>  |
| 生产                        | 本文件 + 你自己的编排                        | 必须开鉴权、换密钥、终止 TLS、备份数据库        |

## 1. 组成

```text
浏览器 :8080
  └─ web 容器(nginx:alpine，deploy/web/nginx.conf)
       ├─ /            → /usr/share/nginx/html(静态 SPA，深链回退 index.html)
       ├─ /api/        → bff:8007(SSE 不缓冲、长超时)
       └─ /healthz /readyz → bff:8007(探针，不落 SPA 回退)
            └─ bff:8007 → account-service:8001 / agent-orchestrator:8002
                         / llm-gateway:8003 / connector-service:8004
                         / workspace-service:8005 / knowledge-service:8006
                         / tool-service:8008(内置工具服务)
postgres:5432(6 个库，见 §4) · redis:6379(预留) · migrate(一次性作业)
```

- Python 侧用**同一个镜像**跑 8 个服务：构建参数 `SERVICE` 决定入口模块与端口（构建方式见 [deploy/docker/python-service.Dockerfile](../docker/python-service.Dockerfile)）。理由：单 uv workspace、单 `uv.lock`、根 `pyproject.toml` 依赖全部服务，一次 `uv sync --frozen --no-dev` 即可装齐运行依赖。
- Web 镜像：Node 构建 `apps/web` 静态产物 → nginx:alpine，配置文件来自本目录 `nginx.conf`（见 [deploy/docker/web.Dockerfile](../docker/web.Dockerfile)）。
- 配置全部走环境变量（各服务 pydantic-settings 前缀：`ACCOUNT__`、`AGENT_ORCHESTRATOR__`、`BFF__` …），**没有把密钥写进镜像**。

## 2. 快速开始

```sh
# 在仓库根执行。首次会构建两个镜像(数分钟，取决于网络)
docker compose -f deploy/docker-compose.yml up -d --build

# 看状态(等所有服务 healthy；migrate 应显示 exited(0))
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs -f migrate   # 6 个库的迁移日志

# 打开 http://localhost:8080
# 自定义演示密钥(可选，不传则用 compose 里的演示默认值)
printf 'INTERNAL_SECRET=%s\nBFF_SESSION_SECRET=%s\n' "$(openssl rand -hex 32)" "$(openssl rand -hex 32)" > deploy/.env
docker compose -f deploy/docker-compose.yml --env-file deploy/.env up -d --build

# 停止 / 连数据一起清空
docker compose -f deploy/docker-compose.yml down
docker compose -f deploy/docker-compose.yml down -v   # 删除 pgdata 与两个业务卷
```

说明：

- compose 的构建上下文是**仓库根**（`context: ..`），所以必须在仓库根或任意目录用 `-f deploy/docker-compose.yml` 调用均可，但不要 `cd deploy` 后省略 `-f` 去构建上下文。
- `down -v` 会删掉 `pgdata`（6 个库）、`account-keys`（账号服务签名密钥）、`workspace-artifacts`（交付物）。下一步 `up` 会重新执行 `deploy/postgres/initdb/01-create-databases.sql`。
- 宿主机不暴露 BFF/各服务端口，浏览器只访问 `:8080`；服务间走 compose 默认网络。
- 只需要一个镜像时：

```sh
docker build -f deploy/docker/python-service.Dockerfile --build-arg SERVICE=bff -t beauty-moon/bff:local .
docker build -f deploy/docker/web.Dockerfile -t beauty-moon/web:local .
```

## 3. 数据库与迁移

6 个有迁移的服务（account / agent-orchestrator / connector / knowledge / workspace / tool）**各自一个数据库**：

| 服务               | 库名               | 环境变量                           |
| ------------------ | ------------------ | ---------------------------------- |
| account-service    | `beauty_account`   | `ACCOUNT__DATABASE_URL`            |
| agent-orchestrator | `beauty_agent`     | `AGENT_ORCHESTRATOR__DATABASE_URL` |
| connector-service  | `beauty_connector` | `CONNECTOR__DATABASE_URL`          |
| knowledge-service  | `beauty_knowledge` | `KNOWLEDGE__DATABASE_URL`          |
| workspace-service  | `beauty_workspace` | `WORKSPACE__DATABASE_URL`          |
| tool-service       | `beauty_tool`      | `TOOL__DATABASE_URL`               |

库由 [deploy/postgres/initdb/01-create-databases.sql](../postgres/initdb/01-create-databases.sql) 在**数据卷首次初始化**时创建。为什么隔离：六个服务的 alembic 都用默认版本表名 `alembic_version`（`packages/common/common/alembic_kit` 未配置 `version_table`），共用一个库会互相覆盖版本记录，迁移直接乱序。即使以后换成独立 schema，也建议保持这六个库名。

迁移由一次性作业 `migrate` 负责：等 postgres healthy 后按固定顺序对 6 个库执行 `alembic upgrade head`，成功退出后 6 个业务服务才启动（`depends_on: migrate: service_completed_successfully`）。服务本身 **`AUTO_CREATE_TABLES=false`**，不做运行期建表。

```sh
# 手动补一次迁移(改了迁移文件后)
docker compose -f deploy/docker-compose.yml run --rm migrate
# 单独进某个服务目录跑 alembic
docker compose -f deploy/docker-compose.yml run --rm --entrypoint sh migrate -c "cd /app/services/account-service && alembic current"
```

**新增迁移的流程**（与裸机一致）：改模型 → 在服务目录生成迁移 → `uv run alembic check` 无差异 → rebuild 镜像 → `up` 时 migrate 作业自动升级。生产回滚时 `alembic downgrade` 与镜像回退必须成对执行（见 §8）。

## 4. 配置

### 4.1 演示默认值与覆盖方式

compose 里所有可覆盖项都写成 `${VAR:-演示默认值}`，可用环境变量或 `deploy/.env` 覆盖。**演示默认值仅供 `compose up` 开箱可用，不是生产值**：

| 变量                                                                         | 用途                                                                            | 生产要求                                                                                 |
| ---------------------------------------------------------------------------- | ------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------- |
| `INTERNAL_SECRET`                                                            | BFF/account/AO/gateway/knowledge 内部调用共享密钥（**同时是 BYOK 解析总开关**） | 随机长串，六处必须一致（compose 已共享同一变量）；留空则 BYOK 静默停用                   |
| `BFF_SESSION_SECRET`                                                         | BFF Cookie 会话签名                                                             | 随机长串；**不能等于**默认值 `dev-bff-session-secret-change-me`，否则 `/readyz` 返回 503 |
| `MCP_SECRET`                                                                 | MCP 相关签名                                                                    | 随机长串                                                                                 |
| `CONNECTOR_CREDENTIAL_SECRET`                                                | 连接器凭证加密                                                                  | 随机长串；换值后旧凭证无法解密                                                           |
| `LLM_GATEWAY_API_KEY` / `LLM_GATEWAY_API_BASE` / `LLM_GATEWAY_DEFAULT_MODEL` | 模型网关                                                                        | 真实 key；`LLM_ENABLED=true` 才真正调用模型                                              |
| `LLM_GATEWAY__ACCOUNT_BASE_URL`                                              | 网关向 account 解析 BYOK 的地址                                                 | 容器内必须为 `http://account-service:8001`（默认 `127.0.0.1:8001` 在容器里指向网关自身） |
| `ACCOUNT__BYOK_KMS`                                                          | BYOK 密钥托管方式                                                               | `local`（默认）或 `vault`；生产建议 `vault` + `ACCOUNT__BYOK_VAULT_URL/TOKEN`            |
| `ACCOUNT__BYOK_SECRET`                                                       | local 模式 KEK 密钥材料                                                         | 随机长串；**不能等于** `dev-byok-secret-change-me`；换值前先按 BYOK 设计的轮换步骤做     |
| `LLM_GATEWAY__BYOK_ENABLED`                                                  | 网关是否启用租户 key 解析                                                       | `true`（默认）；出问题置 `false` 即可一键回退平台 key                                    |
| `VITE_AUTH_MODE`                                                             | web 构建期鉴权模式                                                              | 默认 `session`（同源 BFF Cookie）；见 §4.2                                               |
| `READY_PROBE_UPSTREAMS`                                                      | BFF `/readyz` 是否探上游                                                        | 编排层想用就绪探针时设 `true`                                                            |

**BYOK（租户自带模型密钥）部署要点**：契约见 [docs/design/byok.md](../../docs/design/byok.md)，服务侧操作见 [account-service README](../../services/account-service/README.md) 与 [llm-gateway README](../../services/llm-gateway/README.md)。容器里有两处**必须显式配**：

- `LLM_GATEWAY__INTERNAL_SECRET`——compose 已从 `INTERNAL_SECRET` 同源注入；留空等于关闭 BYOK（同时模型端点也不鉴权，仅本地 dev 可接受）；
- `LLM_GATEWAY__ACCOUNT_BASE_URL=http://account-service:8001`——compose 已提供该覆盖（2026-10-26 补齐）；自建编排必须显式补，否则网关解析全部失败、静默回退平台 key（表现就是「BYOK 保存了但不生效」）。

换 `ACCOUNT__BYOK_SECRET`（或从 `local` 切 `vault`）前先按 [docs/design/byok.md](../../docs/design/byok.md) §6.5 的轮换步骤加 `ACCOUNT__BYOK_PREVIOUS_SECRETS` 并跑 `POST /v1/byok/rotate`，否则存量密文会 `byok.unreadable`；网关侧最多 `BYOK_CACHE_TTL_SECONDS`（默认 300s）后生效。

`VITE_*` 是**构建期**变量：改了要 `up -d --build web` 重新构建，运行期改环境变量无效。

### 4.2 AUTH_ENABLED 与生产差异

compose 默认给所有服务 `AUTH_ENABLED=false`，这是**演示模式**：

- 业务服务不校验 JWT（不向 account-service:8001 做 JWKS 发现），请求同源走 BFF；
- 但 BFF 的 `/api/auth/*` 仍然工作（注册/登录/refresh），account-service 的签名密钥在容器内生成并持久化到 `account-keys` 卷（`ACCOUNT__SIGNING_KEY_PATH=/data/keys/account-signing-key.pem`，目录见 [python-service.Dockerfile](../docker/python-service.Dockerfile) 的 `/data/keys` 预建）；
- web 默认 `VITE_AUTH_MODE=session`：登录请求走同源 `/api/auth/*`，双令牌只放 httpOnly Cookie。不能默认 `token` 模式——token 模式前端要直连 account-service:8001，而本 Nginx 只反代 `/api`，浏览器没有路由且会撞 CORS。

生产切法：

```yaml
# deploy/docker-compose.override.yml 片段(自建 overlay)
services:
  account-service:
    environment:
      ACCOUNT__AUTH_ENABLED: 'true'
      ACCOUNT__OAUTH21_WEB_ORIGIN: 'https://app.example.com'
  agent-orchestrator:
    environment:
      AGENT_ORCHESTRATOR__AUTH_ENABLED: 'true'
      AGENT_ORCHESTRATOR__AUTH_ISSUER: 'https://api.example.com'
      AGENT_ORCHESTRATOR__AUTH_JWKS_URL: 'https://api.example.com/.well-known/jwks.json'
  bff:
    environment:
      BFF__COOKIE_SECURE: 'true'
      BFF__APP_ORIGIN: 'https://app.example.com'
      BFF__RUNTIME_PUBLIC_URL: 'https://app.example.com'
```

注意：

- 开了鉴权后各服务的 issuer/JWKS 必须指向同一个 account-service 公网地址；BFF 的 Cookie `Secure` 必须配 TLS（§6）。
- `ACCOUNT__SIGNING_KEY_PATH` 必须落在持久卷（compose 已用 `account-keys`）。若该文件丢失，重启后签名密钥变化，所有已签发 token / Cookie 立即失效（表现为全站重登）。
- 演示默认的 `BFF__COOKIE_SECURE=false` 只适用于 HTTP 本地演示。

## 5. 裸机 vs 容器

| 项             | 裸机（[docs/local-dev.md](../../docs/local-dev.md)） | 容器                                            |
| -------------- | ---------------------------------------------------- | ----------------------------------------------- |
| 启动           | `npm run dev`（`scripts/dev.sh`）                    | `docker compose ... up -d --build`              |
| 数据库         | 各服务目录下 SQLite（`*.db`）                        | postgres:16，6 个库                             |
| 迁移           | 服务启动内置表或手动 alembic                         | `migrate` 一次性作业                            |
| Nginx upstream | `127.0.0.1:8007`                                     | `bff:8007`（[nginx.conf](./nginx.conf) 默认值） |
| 端口           | 8001–8008 + 5173                                     | 仅 `8080`（+ 5432/6379 给本机调试）             |
| 数据重置       | `npm run dev:reset`                                  | `down -v`                                       |
| 热更新         | 有（Vite/Nx）                                        | 无，重建镜像                                    |

若在容器里只跑 BFF 而 web 用裸机 Nginx，把 [nginx.conf](./nginx.conf) 的 `upstream bff_upstream` 改成宿主机地址（容器网络之外）。

## 6. TLS 两条路

1. **上层负载均衡 / 网关终止 TLS**（推荐）：LB 监听 443 → 转发到本栈 `:8080`（HTTP）。此时只暴露 web 端口，BFF 不对外；记得给 LB 传 `X-Forwarded-Proto` 并设 `BFF__COOKIE_SECURE=true`。
2. **Nginx 容器内终止 TLS**：启用 [nginx.conf](./nginx.conf) 末尾注释的 443 server 块（约 :96-111），把 80 改成跳转，挂载证书到 `/etc/nginx/certs/fullchain.pem`、`/etc/nginx/certs/privkey.pem`。compose 需补挂载与 443 端口：

```yaml
services:
  web:
    ports: ['80:80', '443:443']
    volumes:
      - ./certs:/etc/nginx/certs:ro
```

两条路都必须把 `BFF__COOKIE_SECURE` 设为 `true`（否则 Cookie 在 HTTPS 页面不会被发送），并同步改 `BFF__APP_ORIGIN`、`BFF__RUNTIME_PUBLIC_URL` 为公网 https 地址。

## 7. 健康检查与就绪

- 每个容器 `HEALTHCHECK` 打自己的 `/healthz`（**进程存活**，不依赖 DB/下游），Python 镜像用标准库 `urllib`（runtime 没有 curl），web 用 `wget` 打静态首页。
- `/readyz` 是**就绪**语义：BFF 检查关键配置（session/internal 密钥齐全且非默认值），可选 `READY_PROBE_UPSTREAMS=true` 时再探下游；各业务服务返回自身依赖状态。nginx 已把这两个路径反代到 BFF，不会被 SPA 回退吃掉。
- compose 内部只把 `service_healthy` 当作启动顺序依据（用 `/healthz`）。如果编排层（K8s/ECS）要做流量摘除，用 `/readyz` 而不是 `/healthz`。
- 探针约定与日志降噪见 [docs/observability.md](../../docs/observability.md)。

## 8. 升级、回滚与备份

```sh
# 升级：改代码后重建并滚动重启(compose 会按 depends_on 顺序)
docker compose -f deploy/docker-compose.yml up -d --build

# 只重建某个服务
docker compose -f deploy/docker-compose.yml up -d --build bff

# 回滚镜像：生产建议给镜像打不可变 tag(如 commit sha)而不是依赖 :local
docker tag beauty-moon/bff:local beauty-moon/bff:prev
# 回滚迁移(在服务目录内，先确认目标 revision)
docker compose -f deploy/docker-compose.yml run --rm --entrypoint sh migrate \
  -c "cd /app/services/account-service && alembic downgrade -1"

# 数据库备份/恢复(建议升级前必做)
docker compose -f deploy/docker-compose.yml exec postgres pg_dumpall -U beauty > backup.sql
cat backup.sql | docker compose -f deploy/docker-compose.yml exec -T postgres psql -U beauty
```

原则：**镜像回退与 `alembic downgrade` 成对**；不可逆迁移（删列/删表）只能靠备份恢复。生产不要用 `:local` 浮动 tag，用 `<registry>/beauty-moon/<service>:<git-sha>`。

## 9. 排障

| 现象                          | 排查                                                                                                                                                                                    |
| ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `up` 卡在 migrate             | `docker compose -f deploy/docker-compose.yml logs migrate`；常见是数据库连接串/库不存在（要 `down -v` 重新 initdb）                                                                     |
| 服务 unhealthy                | `docker compose ... logs <service>`；`/healthz` 只证明进程活着，业务错看日志/500                                                                                                        |
| web 打开白屏                  | 镜像是否重建（`VITE_*` 是构建期的）；`logs web` 看 nginx                                                                                                                                |
| `/api` 502                    | BFF 未健康；先 `logs bff`，再看它依赖的七服务                                                                                                                                           |
| `/readyz` 503                 | 多半是 `BFF_SESSION_SECRET` 用了默认值或 `INTERNAL_SECRET` 为空，改 `deploy/.env` 后 `up -d bff`                                                                                        |
| BYOK 保存了但一直用平台 key   | 看网关日志有无 `byok_resolved`；确认 `LLM_GATEWAY__ACCOUNT_BASE_URL=http://account-service:8001`、内部密钥与 account 一致；指标 `llm_byok_lookups_total{status="error"}` 抬头即解析失败 |
| 迁移报 `alembic_version` 冲突 | 说明复用了同一个库；确认 `*__DATABASE_URL` 六个各自指向 `beauty_<服务>`                                                                                                                 |
| 构建很慢/上下文巨大           | 仓库根缺 `.dockerignore`（见下节建议）                                                                                                                                                  |

## 10. 建议补的仓库根 `.dockerignore`

当前仓库根没有 `.dockerignore`，本地构建会把 `node_modules`、`.venv`、`.git`、缓存打进构建上下文（CI 干净 checkout 不受影响，本地明显变慢）。建议内容（W4 后续落地）：

```text
node_modules
.venv
.nx
.cache
.npm-cache
.uv-cache
.uv-python
.git
.github
reports
coverage
dist
**/dist
**/out-tsc
**/doc_build
**/.artifacts
**/*.db
docs
```

注意不要忽略 `deploy/`（镜像要 COPY `deploy/web/nginx.conf`）与 `packages/`、`services/` 的源码。

## 11. 未包含（W4 范围外）

- redis 容器已保留但**当前代码没有 redis 依赖**（限流在进程内），仅作后续扩展预留。
- 未部署 `apps/site`（Next.js 官网，仍按原方案走 Vercel 静态导出）与 `apps/desktop`（Electron 本地模式）。
- 没有对象存储（workspace-service 用文件系统卷 `workspace-artifacts`）。
- 沙箱（microsandbox）在容器内未启用（`WORKSPACE__SANDBOX_REAPER_ENABLED=false`）。
