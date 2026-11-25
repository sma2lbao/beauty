# deploy

部署与容器化相关资产的总入口。日常开发不要把这里当成启动脚本入口——本地热更新开发用
`npm run dev`（见 [docs/local-dev.md](../docs/local-dev.md)）。

| 路径                                                                   | 内容                                                                                                                                                                                                                              |
| ---------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [docker-compose.yml](./docker-compose.yml)                             | 全栈演示/自托管编排：web(nginx) + 9 个 Python 服务 + eval-retention 常驻清理作业 + postgres + redis + 迁移作业，单入口 <http://localhost:8080>；只起基础设施用 `docker compose -f deploy/docker-compose.yml up -d postgres redis` |
| [docker/python-service.Dockerfile](./docker/python-service.Dockerfile) | 9 个 Python 服务共用的镜像，靠 `SERVICE` 选入口；HEALTHCHECK 打 `/healthz`                                                                                                                                                        |
| [docker/web.Dockerfile](./docker/web.Dockerfile)                       | Web 静态资源构建（Vite 产物）+ nginx 运行时                                                                                                                                                                                       |
| [web/](./web)                                                          | nginx 配置与「生产差异」说明（鉴权、TLS、密钥、备份），见 [web/README.md](./web/README.md)                                                                                                                                        |
| [postgres/initdb/](./postgres/initdb)                                  | 首次初始化建 7 个业务库（一服务一库，避免 `alembic_version` 互相覆盖）                                                                                                                                                            |
| [otel-collector/config.yaml](./otel-collector/config.yaml)             | OTel Collector 尾部采样，把 trace 汇聚后转发 Langfuse                                                                                                                                                                             |
| [tencent/](./tencent)                                                  | 生产主线：腾讯云轻量（首尔）应用机 + 同 VPC 基础设施机的 Caddy/Compose/脚本，见 [tencent/README.md](./tencent/README.md)                                                                                                          |

生产架构取舍（Vercel 静态层 + 腾讯云应用机 + 基础设施机的分工）见
[docs/design/hybrid-deployment.md](../docs/design/hybrid-deployment.md)。
镜像发布属部署流程，不在 `nx release` 范围（见 [docs/release.md](../docs/release.md)）。
