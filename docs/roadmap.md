# 活跃事项与路线图

> 未完成计划的唯一真源（取代已归档的 feature-gap.md / tasks-m0\*.md）。
> 重构波次执行状态另见 [refactoring/roadmap.md](./refactoring/roadmap.md)；本文只跟踪产品/平台侧活跃事项。

## 当前活跃

- **~~M0-02 七服务容器化~~ ✅ 已完成**：`deploy/docker/python-service.Dockerfile`（7 个 Python 服务复用，`SERVICE` 选入口）+ `deploy/docker/web.Dockerfile` + `deploy/docker-compose.yml` 全量编排（pg/redis/migrate/7 服务/web，:8080）；CI 增 `docker-build` job（= 重构 R-36 关闭）。
- **M0-03 云 PG/云 Redis 接入**：生产 URL 注入即用；开发保持 SQLite/本地 Redis；CI 对云 PG 跑迁移与集成测试；顺带把限流从进程内切到 Redis（R-P1-8 缺口：compose 已起 Redis、业务未接）。
- **~~CI 上线后~~ ✅ 已完成**：覆盖率阈值（R-31）、零网络硬门禁（R-32）、文档链接检查（`scripts/check-docs.sh`，CI `docs-check` job）。
- **agent 服务能力补齐（待决策）**：2026-10-25 复核出的 P0/P1 差距与建议排期见 [gap-2026-10/06-refresh-2026-10-25.md](./gap-2026-10/06-refresh-2026-10-25.md) §3/§6；其中 P0 项（成本归因、run 生命周期、多实例正确性、对话配额闸、MCP SSRF、BYOK 决策）决策后并入本表。

## 暂缓（写明恢复条件）

- **M0-13 内网/离线部署**：恢复条件=有内网交付需求；方案见 [architecture.md §10.3](./architecture.md) 与归档的 [tasks-m0-13.md](./archive/2026-10/tasks-m0-13.md)。
- Temporal/Inngest、对象存储、Stripe、Resend、Langfuse、多区域：见 [architecture.md §12](./architecture.md)。
