# 重构路线图

- 来源：docs/refactoring-playbook.md 第 10 节
- 当前波次：W1 已收口（2026-10-24），W2 待立项；最近更新：2026-10-24 23:50 +08:00
- 历史：W0 见 docs/refactoring/retro/w0.md，W1 见 docs/refactoring/retro/w1.md 与 docs/refactoring/verification/002-w1.md

## W0（已收口 · 7 项）

| ID   | 重构项                      | owner   | 状态    | 提交    |
| ---- | --------------------------- | ------- | ------- | ------- |
| R-01 | Python target 环境固化      | w0-gate | ✅ done | 5f4fb8a |
| R-02 | 根 scripts/engines/.nvmrc   | lead    | ✅ done | 5f4fb8a |
| R-03 | 最小 CI                     | w0-ci   | ✅ done | ab2c4f3 |
| R-04 | Python implicitDependencies | w0-gate | ✅ done | 5f4fb8a |
| R-05 | 文档单一真源                | w0-docs | ✅ done | c9b3d63 |
| R-06 | PR 模板 + 提交规范          | w0-ci   | ✅ done | ab2c4f3 |
| R-07 | 门禁脚本                    | w0-gate | ✅ done | 40c74c3 |

## W1（已收口 · 9 项）

- 立项：8ba4217（RFC-002 + baseline「W1 基线」）
- 出口门禁（§10.3 W1）：request<T>=0 ✅；getReader=1 ✅；import.meta.env=0 ✅；queryKey 字面量=0 ✅；format 定义各 1 ✅；单 PR ≤400 行（3 例外）⚠️；bundle 新基线入库 ✅

| ID   | 重构项                               | 规模 | 风险 | owner    | 状态    | 提交    | 出口证据                               |
| ---- | ------------------------------------ | ---- | ---- | -------- | ------- | ------- | -------------------------------------- |
| R-08 | env 集中（14 处 → config/env.ts）    | S    | 低   | w0-gate  | ✅ done | 7604bd6 | import.meta.env 散落=0                 |
| R-09 | queryKey registry（58 → 工厂）       | S    | 低   | w0-gate  | ✅ done | f8150b3 | 字面量=0；回滚演练通过                 |
| R-10 | 路由级拆包（10 页 lazy + Suspense）  | S    | 低   | lead     | ✅ done | dfbbb8b | 入口 chunk 737954B→315502B             |
| R-11 | HTTP client 工厂（10 份 → 1）        | M    | 低   | w1-api   | ✅ done | 4091b71 | request<T>=0；http.test 3 用例         |
| R-12 | SSE 基座（3 份 reader → lib/sse.ts） | M    | 中   | w1-api   | ✅ done | 4091b71 | getReader=1（sse.ts:76）               |
| R-13 | 拆 presentation.ts + 去重 format     | S    | 低   | audit-ts | ✅ done | 4eba612 | 477 行 → barrel 11 + 5 模块            |
| R-14 | eslint 边界与体量规则                | S    | 低   | w0-ci    | ✅ done | 593f9c7 | 探针命中；web 0 error 0 warn           |
| R-15 | dev.sh / reset 脚本健壮性            | M    | 低   | w0-ci    | ✅ done | 7727435 | 4 缺陷全修；bash 3.2 语法通过          |
| R-16 | 文档门禁（prettier + 链接检查）      | S    | 低   | w0-ci    | ✅ done | a67fa44 | 23 文件 0 失败；入 gate 与 CI（7 job） |

### W1 出口检查

- [x] 门禁实测：全仓 lint 15/15、web typecheck/lint/test/build 全绿、desktop 58、design 95、common pytest 71
- [x] 指标全部达成（详见 docs/refactoring/verification/002-w1.md §2）
- [x] 回滚演练 1 次成功（f8150b3，24 文件干净回退）
- [x] 复盘写入 docs/refactoring/retro/w1.md；playbook v1.3 记录两条流程改进
- [x] W2 可立项（R-17 所依赖的 R-08/09/11/12 已就绪）

## 遗留项（跟踪）

| 项                                                                                         | 来源               | 跟踪                                        |
| ------------------------------------------------------------------------------------------ | ------------------ | ------------------------------------------- |
| W1 push 后 CI 7 job 首跑验证（含新增 docs-check job）                                      | R-16               | 结果出来回填本行                            |
| 四页既有 Prettier 漂移：knowledge / workspace / outbox / mcp                               | R-13               | 随 R-18 拆分时一并归零                      |
| workspace-service test_sandbox_lifecycle 既有环境失败（本机 msb rootfs 缓存）              | W0 gate --affected | R-15 已改进清理脚本，根因待后续环境治理     |
| @nxlv/python 6 类 executor（sync/lock/add/update/remove/install、ruff-check/format）无 env | R-01 降级方案      | 上游支持后回补 project.json；gate/CI 已兜底 |
| common:build（uv export）在无 UV_CACHE_DIR 时失败（build executor 不吃 env）               | R-01               | gate/CI 均先导出，非阻塞                    |
| 根 eslint.config.mjs 仅 base()，webLayerBoundary 只在项目 cwd 生效                         | R-14               | 已在 base.js 注释说明；R-21 收敛时复核      |

## W2（待立项 · 2026-10-24 预排）

- 范围：R-17 ~ R-26（docs/refactoring-playbook.md §10.2 W2 表）
- 门禁（§10.3 W2）：W1 全部门禁 + Python service-kit 试点服务 lint/test + 全仓 TS 测试；指标：chat.tsx ≤400、console-ui 被两端引用、connector/knowledge 启动路径一致；复盘重点：拆分等价性（黄金路径人工核对）
- 体积例外（§10.4）：R-17 / R-19 / R-22 先合 characterization 或契约快照 PR，再逐 PR 拆。

| ID   | 重构项                                                  | 规模 | 风险 | 依赖                    | 状态   |
| ---- | ------------------------------------------------------- | ---- | ---- | ----------------------- | ------ |
| R-17 | chat.tsx 1313 行拆 features/chat/\*（+ 901 行测试重写） | L    | 高   | R-08/09/11/12 ✅        | 待立项 |
| R-18 | mcp 941 / sidebar 663 / workspace 610 / settings 472    | M    | 中   | R-13、R-17 后端模式成立 | 待立项 |
| R-19 | 抽 @beauty/console-ui（web + desktop 共享）             | L    | 中   | R-17（避免同文件冲突）  | 待立项 |
| R-20 | 清 web/site 重复（button-class、utils、logo、About）    | S    | 低   | 无                      | 待立项 |
| R-21 | tsconfig/vitest 收敛 + site/console 测试盲区            | M    | 中   | R-14                    | 待立项 |
| R-22 | Python service-kit 试点（connector + knowledge）        | L    | 中   | R-25                    | 待立项 |
| R-23 | service_testkit（统一 conftest / 零网络默认）           | M    | 中   | R-22                    | 待立项 |
| R-24 | alembic 共享 env 模板 + 迁移 smoke                      | M    | 中   | R-22                    | 待立项 |
| R-25 | 收编 Fernet×4 / SlidingWindowLimiter×2 / 内部密钥校验   | S    | 低   | 无（随 R-22 落地）      | 待立项 |
| R-26 | design 令牌单一来源 + Dialog/Button 合一                | M    | 中   | 无                      | 待立项 |

## 波次总览

| 波次 | 主题                                                   | 状态                  |
| ---- | ------------------------------------------------------ | --------------------- |
| W0   | 门禁与真源（R-01..R-07）                               | ✅ 收口（2026-10-24） |
| W1   | 前端基座与工具链（R-08..R-16）                         | ✅ 收口（2026-10-24） |
| W2   | 页面拆分 + 跨端共享 + service-kit 试点（R-17..R-26）   | 待立项                |
| W3   | agent-orchestrator 深水区 + 质量 ratchet（R-27..R-33） | 未开始                |
| W4   | 发布与运维（R-34..R-38）                               | 未开始                |
