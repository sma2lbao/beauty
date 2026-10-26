# 文档索引

> 本文件是 docs/ 的唯一入口：一份文档管一件事，先看这里再找文档（playbook §12.2 地图落地）。
> 历史文档在 [archive/](./archive/)，只留痕、不更新、不作为依据。新增/下线文档时更新本表。

## 当前文档（单一真源）

| 路径                                                                      | 角色                                                                      | 维护者     | 更新时机                   |
| ------------------------------------------------------------------------- | ------------------------------------------------------------------------- | ---------- | -------------------------- |
| [AGENTS.md](../AGENTS.md)                                                 | 工程约定（命令/风格/禁止事项）                                            | Lead       | 约定变化时                 |
| [docs/README.md](./README.md)                                             | 文档索引（本文件，唯一入口）                                              | Lead       | 文档增删时                 |
| [docs/architecture.md](./architecture.md)                                 | 目标架构（唯一现行版本：服务边界/数据流/技术选型/ADR 001-011）            | Lead       | 架构变更（配 ADR）         |
| [docs/roadmap.md](./roadmap.md)                                           | 活跃事项与未完成计划（唯一）                                              | Lead       | 每波开始/结束              |
| [docs/refactoring-playbook.md](./refactoring-playbook.md)                 | 重构流程（流程/门禁/模板/指标）                                           | Lead       | 波次复盘后                 |
| [docs/refactoring/rfc/](./refactoring/rfc/)                               | 单个重构项的 RFC（立项时冻结）                                            | 该片 owner | 立项时                     |
| [docs/refactoring/roadmap.md](./refactoring/roadmap.md)                   | 重构波次路线图与执行状态                                                  | Lead       | 每波开始/结束              |
| [docs/refactoring/baseline.md](./refactoring/baseline.md)                 | 重构基线表                                                                | Lead       | 每波结束时                 |
| [docs/local-dev.md](./local-dev.md)                                       | 本地开发与 troubleshooting                                                | Lead       | 脚本或端口变化时           |
| [docs/refactoring/verification/](./refactoring/verification/)             | 每波验证报告（门禁实测 + 指标对比）                                       | verifier   | 波次收口时                 |
| [docs/refactoring/retro/](./refactoring/retro/)                           | 每波复盘（教训与流程改进）                                                | Lead       | 波次收口时                 |
| [docs/observability.md](./observability.md)                               | 日志/指标/追踪约定（可观测性契约）                                        | Lead       | 观测设施变化时             |
| [docs/design/tool-service.md](./design/tool-service.md)                   | 内置工具服务设计（catalog/执行/审计/ask_question，含 OSS 对比与实施步骤） | Lead       | 工具契约或实施进度变化时   |
| [docs/design/serverless-deployment.md](./design/serverless-deployment.md) | serverless 部署方案（平台对比/改造清单/分期）                             | Lead       | 部署形态或平台变化时       |
| [docs/design/byok.md](./design/byok.md)                                   | BYOK 设计（信任边界/解析与回退/信封 v2 与 KMS/轮换/配置/威胁模型）        | Lead       | BYOK 契约或 KMS 形态变化时 |
| [docs/gap-2026-10/](./gap-2026-10/)                                       | 能力缺口需求池（未落地的能力缺口）                                        | Lead       | 缺口状态变化时             |
| [apps/\*/README.md](../apps/)                                             | 应用运行说明                                                              | 应用 owner | 应用行为变化时             |
| [.github/PULL_REQUEST_TEMPLATE.md](../.github/PULL_REQUEST_TEMPLATE.md)   | PR 模板                                                                   | Lead       | 流程变化时                 |

## 归档文档（不作为依据）

| 路径                                                                       | 说明                                     | 取代者                                                                  |
| -------------------------------------------------------------------------- | ---------------------------------------- | ----------------------------------------------------------------------- |
| [archive/2026-10/architecture-v2.md](./archive/2026-10/architecture-v2.md) | 架构 v2（决策版 2026-10-18，本文基底）   | [architecture.md](./architecture.md)                                    |
| [archive/2026-10/feature-gap.md](./archive/2026-10/feature-gap.md)         | 旧差距分析（只对比 DeepSeek Harness）    | [gap-2026-10/](./gap-2026-10/) + [architecture.md](./architecture.md)   |
| [archive/2026-10/tasks-m0.md](./archive/2026-10/tasks-m0.md)               | M0 任务清单（2026-10-18 快照，多已完成） | [roadmap.md](./roadmap.md)                                              |
| [archive/2026-10/tasks-m0-13.md](./archive/2026-10/tasks-m0-13.md)         | M0-13 内网/离线部署子计划（暂缓）        | [architecture.md](./architecture.md) §10.3 + [roadmap.md](./roadmap.md) |

## 单一真源约定

- 架构/选型 → [architecture.md](./architecture.md)（唯一）
- 未完成计划 → [roadmap.md](./roadmap.md)（唯一）
- 重构执行状态 → [refactoring/roadmap.md](./refactoring/roadmap.md)
- 本地跑起来 → [local-dev.md](./local-dev.md)（唯一）
- 可观测规范 → [observability.md](./observability.md)（唯一）
- BYOK 契约（存储/解析/回退/KMS） → [design/byok.md](./design/byok.md)（唯一）
- Agent 行为契约 → 根 [AGENTS.md](../AGENTS.md)
- 历史分析全部在 [archive/](./archive/)，不再更新、不作为依据
- 每波重构结束时检查一次是否存在"双真源"（playbook §12.3）
