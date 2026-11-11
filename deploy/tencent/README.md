# 腾讯云轻量（首尔）部署

Vercel（Web 静态）+ 腾讯云轻量应用服务器（8 个 Python 服务 + Caddy）+ Neon（6 个库）。
设计取舍与逐项分析见 [docs/design/hybrid-deployment.md](../../docs/design/hybrid-deployment.md)。

## 1. 机器与系统

| 项     | 选择                                                        |
| ------ | ----------------------------------------------------------- |
| 地域   | 首尔（ap-seoul）                                            |
| 套餐   | 入门型 2C4G 30Mbps 60GB / 1.5TB；活动价 ¥199/年（续费同价） |
| 升级位 | 通用型 4C8G 180GB 35Mbps ¥250/月（并发上来后）              |
| 系统   | Ubuntu 24.04 LTS 64 位纯净镜像                              |
| 安全组 | 只开 22（限自己 IP）、80、443；**不要开 8001–8008**         |

30M 是峰值带宽且官方不保障跨境质量；面向国内用户时延约 50–100ms。若追求更低时延，把机器换到**新加坡**可让 Neon 同区（见 §6）。

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

## 3. 首次部署

```bash
# 1) 代码/编排上机（拷仓库或只拷 deploy/tencent + deploy/docker）
cd /srv/beauty
cp deploy/tencent/.env.example deploy/tencent/.env
vi deploy/tencent/.env          # 填 6 个 Neon URL、域名、密钥、TAG/REGISTRY

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
4. `apps/web` 的 `vercel.json` 已经 rewrite `/api/:path* → https://api.beautymoon.cn/:path*`，无需改。

灰度建议：先用 `api2.beautymoon.cn` 验证全链路，再切生产域名，旧解析保留 24h 便于回滚。

## 5. 发布 / 回滚

```bash
# 发布：改 .env 的 TAG → 重新 pull + 起
bash deploy/tencent/scripts/deploy.sh

# 回滚：把 TAG 改回上一个 sha，再执行同一脚本
```

发布/重启会断开所有 SSE 连接；客户端带 `Last-Event-ID` 自动续流，run 不丢。

## 6. Neon

- Neon 没有首尔/东京区（2026-10 官方列表），亚太只有**新加坡**与悉尼；首尔 → 新加坡 RTT ≈ 70–90ms。
- **可选优化**：若无韩国本地业务要求，把轻量机器放**新加坡**，DB 同区 RTT ≈ 2ms；同样的 2C4G 30M 套餐年付价通常一致。
- 用 pooler 端点（主机名带 `-pooler`）；asyncpg + PgBouncer 事务模式需 `statement_cache_size=0`，且当前代码是 `NullPool`（每请求新建连接），见落地文档 §3.5 的改造项。
- 6 个库一服务一库，**不能合并**（各自 alembic 链）。

## 7. 沙箱

轻量没有 `/dev/kvm`，`microsandbox` 不能跑。当前 `.env` 用：

```dotenv
WORKSPACE__SANDBOX_PROVIDER=unavailable
WORKSPACE__SANDBOX_REAPER_ENABLED=false
```

含义：工具执行返回明确的「沙箱不可用」错误，其余功能正常。

- **禁止**在生产用 `WORKSPACE__SANDBOX_PROVIDER=local`：等于在宿主机直接跑用户代码。
- P0 改造：新增 Modal Sandboxes provider（`workspace/services/sandbox_provider/modal.py`），用 `MODAL_TOKEN_ID`/`MODAL_TOKEN_SECRET` 认证；接口契约与 microsandbox 一致（argv + stdin + 超时）。没有 Modal 账号时，在 [modal.com](https://modal.com) 注册并 `pip install modal && modal token new` 即可拿到 token。
- 该 provider 落地前，Agent 的文件/命令类工具不可用（对话、知识库、连接器不受影响）。

## 8. 备份 / 监控

| 项     | 做法                                                                                 |
| ------ | ------------------------------------------------------------------------------------ |
| 数据库 | Neon 自带 PITR；另加每日 `pg_dump` 到 COS（6 个库分别导）                            |
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
| 工具执行报 `sandbox.unavailable` | 预期行为，待 Modal provider 落地                                                                         |
| 内存被 OOM kill                  | `docker stats` 找峰值；先确认 swap 已开，再把 `mem_limit` 调到 4C8G 机型                                 |

## 10. 已知限制

1. **单机**：发布/重启断 SSE（可续流）、磁盘单点、纵向扩容有上限；50+ 在线后应上多机 + CLB。
2. **2C4G 余量小**：常驻约 2.8G，只够 2–3 个并发 run；团队使用建议直接 4C8G。
3. **30M 跨境质量不保障**：大文件上传/下载、拉镜像（2.5GB ≈ 11 分钟）会慢。
4. **沙箱外置**：Modal 在境外，工具执行多一跳；敏感数据场景要评估。
5. **无对象存储直传**：交付物先落本地卷，Vercel 代理还有 4.5MB 请求上限。

## 11. 与旧文档的关系

- 平台横向对比与「为什么不是 Cloud Run / 全 Vercel」：见 [serverless-deployment.md](../../docs/design/serverless-deployment.md)；
- 本文是**当前落地主线**（2026-11-11），架构决策见 `docs/architecture.md` ADR-012。
