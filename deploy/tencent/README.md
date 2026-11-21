# 腾讯云轻量（首尔）部署

Vercel（Web 静态）+ 腾讯云应用机（8 个 Python 服务 + Caddy）+ 基础设施机（PostgreSQL 16 / Redis / sandboxd）。
设计取舍与逐项分析见 [docs/design/hybrid-deployment.md](../../docs/design/hybrid-deployment.md)。

## 1. 机器与系统

| 项     | 选择                                                        |
| ------ | ----------------------------------------------------------- |
| 地域   | 首尔（ap-seoul）                                            |
| 套餐   | 入门型 2C4G 30Mbps 60GB / 1.5TB；活动价 ¥199/年（续费同价） |
| 升级位 | 通用型 4C8G 180GB 35Mbps ¥250/月（并发上来后）              |
| 系统   | Ubuntu 24.04 LTS 64 位纯净镜像                              |
| 安全组 | 只开 22（限自己 IP）、80、443；**不要开 8001–8008**         |

30M 是峰值带宽且官方不保障跨境质量；面向国内用户时延约 50–100ms。

应用机与基础设施机在**同一个 VPC 内网**（当前 `10.8.0.6/22` ↔ `10.8.0.14`），数据库/Redis/sandboxd 都走内网，不暴露公网。设计见 §6/§7。

初始化：

```bash
apt update && apt -y upgrade
curl -fsSL https://get.docker.com | sh
docker version && docker compose version

# 2GB swap（2C4G 必开）
fallocate -l 2G /swapfile && chmod 600 /swapfile
mkswap /swapfile && swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab

mkdir -p /srv/beauty
```

## 2. 镜像

轻量机器构建太慢，**在本地/CI 构建 amd64 镜像推 TCR，机器只 pull**：

```bash
REGISTRY=ccr.ccs.tencentyun.com/beauty
TAG=$(git rev-parse --short HEAD)

docker build --platform linux/amd64 \
  -f deploy/docker/python-service.Dockerfile \
  --build-arg SERVICE=api-gateway \
  -t "$REGISTRY/beauty/python-service:$TAG" .
docker push "$REGISTRY/beauty/python-service:$TAG"
```

注意：

- 必须 `--platform linux/amd64`，否则 Apple Silicon 上会产出 arm64，轻量上 `exec format error`；
- 8 个服务共用一个镜像，只需构建一次；
- 推完在 `.env` 里同步 `TAG`。

### 1.1 基础设施机（PostgreSQL / Redis / sandboxd）

当前生产把有状态组件与本机 Docker 隔离到**另一台服务器**（同 VPC）：

| 组件          | 监听 | 说明                                                                                                                 |
| ------------- | ---- | -------------------------------------------------------------------------------------------------------------------- |
| PostgreSQL 16 | 5432 | 6 个库 `beauty_<service>`，一服务一库；与 `deploy/postgres/initdb/01-create-databases.sql` 同构，角色 `beauty_admin` |
| Redis         | 6379 | `requirepass`；account-service 动态注册限流、api-gateway 固定窗口限流                                                |
| sandboxd      | 8088 | gVisor（runsc）+ Docker，提供 `/v1/sandboxes` REST 与 PTY WebSocket                                                  |

- 系统同为 Ubuntu 24.04；规格按并发选（4C8G 起步更稳，sandboxd 会跑用户代码）。
- **安全组**：5432 / 6379 / 8088 只对应用机内网 IP 放行，**不要对 `0.0.0.0/0` 开放**。
- 两台机必须能互通（同 VPC/子网，或轻量 → CVM 走云联网）；应用机 `.env` 里 DB/Redis/sandboxd 地址都填基础设施机的内网 IP。
- 数据备份与升级在基础设施机做（见 §8），应用机只负责无状态服务。

## 3. 首次部署

```bash
# 1) 代码/编排上机（拷仓库或只拷 deploy/tencent + deploy/docker）
cd /srv/beauty
cp deploy/tencent/.env.example deploy/tencent/.env
vi deploy/tencent/.env          # 填 6 个 PG URL（基础设施机）、Redis、sandboxd、域名、密钥、TAG/REGISTRY

# 2) 密码/密钥（全部 32 字节 hex）
openssl rand -hex 32             # ACCOUNT__INTERNAL_SECRET / SESSION_SECRET / INTERNAL_SECRET 等

# 3) 迁移（一次性）
bash deploy/tencent/scripts/migrate.sh

# 4) 启动
bash deploy/tencent/scripts/deploy.sh

# 5) 自检
bash deploy/tencent/scripts/verify.sh
```

首次启动时 account-service 会在 `/data/keys/account-signing-key.pem` 自动生成并持久化 RSA 密钥（0600）。**这个文件要备份**：丢了等于所有已签发 token 全部失效。

## 4. DNS / TLS

1. `api.beautymoon.cn` A 记录 → 本机公网 IP；
2. 安全组放行 80/443；
3. Caddy 自动申请证书（HTTP-01 需要 80 可达）；
4. `apps/web` 的 `vercel.json` 已经 rewrite `/api/:path* → https://api.beautymoon.cn/api/:path*`，无需改。

灰度建议：先用 `api2.beautymoon.cn` 验证全链路，再切生产域名，旧解析保留 24h 便于回滚。

## 5. 发布 / 回滚

```bash
# 发布：改 .env 的 TAG → 重新 pull + 起
bash deploy/tencent/scripts/deploy.sh

# 回滚：把 TAG 改回上一个 sha，再执行同一脚本
```

发布/重启会断开所有 SSE 连接；客户端带 `Last-Event-ID` 自动续流，run 不丢。

## 6. 数据库与 Redis（基础设施机）

- PostgreSQL 16 + Redis 都在同 VPC 的基础设施机上（`10.8.0.14`），应用机只连内网，RTT 通常 < 1ms；不再依赖 Neon/云 Redis。
- 6 个库一服务一库，**不能合并**（各自 alembic 链共用默认 `alembic_version` 表）；每个库用独立版本表 `*__ALEMBIC_VERSION_TABLE`。
- 生产 `*__AUTO_CREATE_TABLES=false`，表结构只由 `deploy/tencent/scripts/migrate.sh`（compose 的 `migrate` 作业）推进。
- 内网连接不需要 `?ssl=require`；如基础设施机启用 TLS，再给 URL 加 `?ssl=require` 并配证书校验。
- Redis：`ACCOUNT__REDIS_URL` / `API_GATEWAY__REDIS_URL` 指向基础设施机（`redis://:PASS@10.8.0.14:6379`）；两者可留空回退进程内限流，但多副本前必须接 Redis。
- 排障：应用机 `nc -vz 10.8.0.14 5432/6379/8088`；不通先看安全组与基础设施机监听地址（必须 `0.0.0.0`/内网 IP，不能只听 127.0.0.1）。

## 6.1 从 Neon 迁移

旧部署用的是 Neon pooler + `?ssl=require`；切到自建 PG 时把 6 个 `*__DATABASE_URL` 换成基础设施机 URL（去掉 `?ssl=require`、去掉 `-pooler`），并在 `.env` 里保持同样的 `*__ALEMBIC_VERSION_TABLE`。库内容迁移用 `pg_dump`/`pg_restore`（6 个库分别导）。

## 7. 沙箱

轻量没有 `/dev/kvm`，`microsandbox` 不能跑。生产用外置隔离面，当前选 **`sandboxd`**
（基础设施机上的 gVisor + Docker，本服务通过 REST 管理、WebSocket 提供 PTY）；
托管 microVM（**`e2b`**，E2B 官方云或阿里云 Agent Sandbox 极速版）作为备选。

```dotenv
WORKSPACE__SANDBOX_PROVIDER=sandboxd
SANDBOXD_URL=http://10.8.0.14:8088
SANDBOXD_TOKEN=CHANGE-ME-sandboxd-token
WORKSPACE__SANDBOX_SANDBOXD_IMAGE=python:3.11-slim
WORKSPACE__SANDBOX_SANDBOXD_REQUEST_TIMEOUT_SECONDS=30
WORKSPACE__SANDBOX_REAPER_ENABLED=true
```

- 目标机安装 gVisor（runsc）+ Docker 并运行 sandboxd；应用机只需能访问其内网地址，
  不需要本机 Docker/`/dev/kvm`；
- 保证应用机到 sandboxd 的内网连通，并让 token 只具备创建/执行/删除权限；
- sandboxd 侧需预拉镜像、给 `/workspace` 可写；CPU/内存/TTL 由本服务透传；
- **PTY 走 WebSocket**（`/v1/sandboxes/{id}/pty`）：`open_shell` 可用，文本帧即 stdin；
  但 sandboxd 没有可重连的 session id/pid，`attach_shell` 返回 `sandbox.provider_unsupported`，
  服务重启后会话按 `session_lost` 处理；需要跨重启接管时改用 `e2b`；
- exec 的 `stdin` 由本服务先上传临时文件再重定向到命令（`run_code` 依赖此路径）。

### 备选：e2b（托管 microVM）

不想自建时可用托管 microVM（需 E2B API Key；Hobby 单沙箱寿命 ≤1h）：

```dotenv
WORKSPACE__SANDBOX_PROVIDER=e2b
E2B_API_KEY=CHANGE-ME-e2b-key
# 阿里云极速版（可选）：同时设置
# E2B_API_URL=https://api.cn-hongkong.sandbox.aliyuncs.com
# E2B_DOMAIN=cn-hongkong.sandbox.aliyuncs.com
# 沙箱最大寿命要 ≤ 套餐上限（Hobby 1h）
WORKSPACE__SANDBOX_MAX_TTL_SECONDS=3600
```

两个远端 provider 都没配时保持 `WORKSPACE__SANDBOX_PROVIDER=unavailable`（工具执行返回
明确的「沙箱不可用」错误，其余功能正常）。sandboxd 的 TTL 由本服务透传（默认 7200）；
用 e2b 备选时按套餐调整 `WORKSPACE__SANDBOX_MAX_TTL_SECONDS`（Hobby ≤3600）。

- **禁止**在生产用 `WORKSPACE__SANDBOX_PROVIDER=local`：等于在宿主机直接跑用户代码。
- 其它方案：Modal Sandboxes provider（尚未实现，`sandbox_provider/modal.py`）用 `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` 认证；接口契约与 microsandbox 一致（argv + stdin + 超时）。
- 未配置任何隔离 provider（`e2b` / `sandboxd` / 本机 `microsandbox`）时，Agent 的文件/命令类工具不可用（对话、知识库、连接器不受影响）。

## 8. 备份 / 监控

| 项     | 做法                                                                                 |
| ------ | ------------------------------------------------------------------------------------ |
| 数据库 | 基础设施机每日 `pg_dump`（6 个库分别导）到 COS/另一块盘；要 PITR 就开 WAL 归档       |
| 密钥   | `docker run --rm -v beauty-prod_account-keys:/k alpine tar -C /k -cf - . > keys.tar` |
| 交付物 | `workspace-artifacts` 卷定期打包到 COS（切 COS 直存后可停）                          |
| 监控   | 云监控看 CPU/内存/磁盘；Caddy 访问日志在卷 `caddy-data`；`docker stats` 周检         |

## 9. 排障

| 现象                             | 排查                                                                                                     |
| -------------------------------- | -------------------------------------------------------------------------------------------------------- |
| `exec format error`              | 镜像不是 amd64：用 `--platform linux/amd64` 重建                                                         |
| 502 / `/healthz` 不通            | `docker compose ... logs api-gateway`；看 `depends_on` 服务是否 healthy                                  |
| 登录后立刻掉线 / 401             | `ACCOUNT__AUTH_ISSUER` 与 `API_GATEWAY__RUNTIME_PUBLIC_URL` 是否都是公网 https；Cookie `Secure` 需 HTTPS |
| SSE 不吐字 / 攒一批才出          | Caddy `flush_interval -1` 是否生效；中间不要再加压缩/缓存                                                |
| 迁移报 `alembic_version` 冲突    | 该库被别的服务迁移过；一服务一库，检查 `.env` 的 6 个 URL 没串                                           |
| 工具执行报 `sandbox.unavailable` | 查 `WORKSPACE__SANDBOX_PROVIDER` 与 `SANDBOXD_URL/TOKEN`；应用机 `curl http://10.8.0.14:8088/healthz`    |
| 迁移/启动报 DB 连接失败          | 应用机 `nc -vz 10.8.0.14 5432`；确认 infra PG 监听 `0.0.0.0`、`pg_hba.conf` 放行应用机 IP、安全组放行    |
| 限流/动态注册报 Redis 错误       | `nc -vz 10.8.0.14 6379`；确认 `requirepass` 与 `.env` URL 密码一致；不可用时可留空回退进程内             |
| 内存被 OOM kill                  | `docker stats` 找峰值；先确认 swap 已开，再把 `mem_limit` 调到 4C8G 机型                                 |

## 10. 已知限制

1. **单机**：发布/重启断 SSE（可续流）、磁盘单点、纵向扩容有上限；50+ 在线后应上多机 + CLB。
2. **2C4G 余量小**：常驻约 2.8G，只够 2–3 个并发 run；团队使用建议直接 4C8G。
3. **30M 跨境质量不保障**：大文件上传/下载、拉镜像（2.5GB ≈ 11 分钟）会慢。
4. **沙箱外置**：sandboxd 在基础设施机（同 VPC，多一跳内网）；用户代码不在应用机执行。
5. **无对象存储直传**：交付物先落本地卷，Vercel 代理还有 4.5MB 请求上限。

## 11. 与旧文档的关系

- 平台横向对比与「为什么不是 Cloud Run / 全 Vercel」：见 [serverless-deployment.md](../../docs/design/serverless-deployment.md)；
- 本文是**当前落地主线**（2026-11-11），架构决策见 `docs/architecture.md` ADR-012。
