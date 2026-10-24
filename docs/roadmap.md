# 活跃事项与路线图

> 未完成计划的唯一真源（取代已归档的 feature-gap.md / tasks-m0\*.md）。
> 重构波次执行状态另见 [refactoring/roadmap.md](./refactoring/roadmap.md)；本文只跟踪产品/平台侧活跃事项。

## 当前活跃

- **M0-02 七服务容器化**：补 Dockerfile（当前全仓无）+ compose 补全 + 容器平台部署 + 迁移 job（= 重构 R-36，deploy/）。
- **M0-03 云 PG/云 Redis 接入**：生产 URL 注入即用；开发保持 SQLite/本地 Redis；CI 对云 PG 跑迁移与集成测试；顺带把限流从进程内切到 Redis（R-P1-8 缺口：compose 已起 Redis、业务未接）。
- **CI 上线后**：覆盖率阈值（R-31）、零网络硬门禁（R-32）、文档链接检查纳入门禁（R-16/R-38）。

## 暂缓（写明恢复条件）

- **M0-13 内网/离线部署**：恢复条件=有内网交付需求；方案见 [architecture.md §10.3](./architecture.md) 与归档的 [tasks-m0-13.md](./archive/2026-10/tasks-m0-13.md)。
- Temporal/Inngest、对象存储、Stripe、Resend、Langfuse、多区域：见 [architecture.md §12](./architecture.md)。
