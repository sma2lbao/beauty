# 重构路线图

- 来源：docs/refactoring-playbook.md 第 10 节
- 当前波次：W3 待立项；最近更新：2026-10-25 02:20 +08:00
- 历史：W0 见 docs/refactoring/retro/w0.md，W1 见 docs/refactoring/retro/w1.md 与 docs/refactoring/verification/002-w1.md，W2 见 docs/refactoring/retro/w2.md 与 docs/refactoring/verification/003-w2.md

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

| ID   | 重构项                               | 规模 | 风险 | owner    | 状态    | 提交    | 出口证据                     |
| ---- | ------------------------------------ | ---- | ---- | -------- | ------- | ------- | ---------------------------- |
| R-08 | env 集中（14 处 → config/env.ts）    | S    | 低   | w0-gate  | ✅ done | 7604bd6 | import.meta.env 散落=0       |
| R-09 | queryKey registry（58 → 工厂）       | S    | 低   | w0-gate  | ✅ done | f8150b3 | 字面量=0；回滚演练通过       |
| R-10 | 路由级拆包（10 页 lazy + Suspense）  | S    | 低   | lead     | ✅ done | dfbbb8b | 入口 chunk 737954B→315502B   |
| R-11 | HTTP client 工厂（10 份 → 1）        | M    | 低   | w1-api   | ✅ done | 4091b71 | request<T>=0；http.test 3 例 |
| R-12 | SSE 基座（3 份 reader → lib/sse.ts） | M    | 中   | w1-api   | ✅ done | 4091b71 | getReader=1（sse.ts:76）     |
| R-13 | 拆 presentation.ts + 去重 format     | S    | 低   | audit-ts | ✅ done | 4eba612 | 477 行 → barrel 11 + 5 模块  |
| R-14 | eslint 边界与体量规则                | S    | 低   | w0-ci    | ✅ done | 593f9c7 | 探针命中；web 0 error 0 warn |
| R-15 | dev.sh / reset 脚本健壮性            | M    | 低   | w0-ci    | ✅ done | 7727435 | 4 缺陷全修；bash 3.2 通过    |
| R-16 | 文档门禁（prettier + 链接检查）      | S    | 低   | w0-ci    | ✅ done | a67fa44 | 26 文件 0 失败；入 gate/CI   |

## W2（已收口 · 10 项 + 8 个配套修复）

- 立项：534b03c（RFC-003 + baseline「W2 基线」）；映射修正 c16cd01、72b70a3
- 出口门禁（§10.3 W2）：chat.tsx ≤400 ✅（5 行）；console-ui 被两端引用 ✅（web + desktop）；connector/knowledge 启动路径一致 ✅（service_kit 同构 create_app）；全仓 lint 16/16 ✅；全仓 TS 测试 5 项目 498 例 ✅；Python 10 项目 724 例 ✅

| ID   | 重构项                                         | 规模 | 风险 | owner        | 状态    | 提交    | 出口证据                               |
| ---- | ---------------------------------------------- | ---- | ---- | ------------ | ------- | ------- | -------------------------------------- |
| R-17 | chat.tsx 1320 行 → features/chat/\*            | L    | 高   | w1-api       | ✅ done | 188e4f9 | pages/chat.tsx 5 行；27 例迁移         |
| R-18 | mcp/sidebar/workspace/settings 四页拆分        | M    | 中   | audit-ts     | ✅ done | eba95c0 | 68/84/93/81 行；features 目录 5 个     |
| R-19 | 抽 @beauty/console-ui（web + desktop）         | L    | 中   | w1-api       | ✅ done | 5273f58 | 双端引用；diff-view 169→13             |
| R-20 | site 去重（utils/brand/按钮配方）              | S    | 低   | lead         | ✅ done | d0205d3 | next 静态导出 9 页；含 bd-brand-\*     |
| R-21 | tsconfig/vitest 收敛 + site/console 测试       | M    | 中   | w0-ci        | ✅ done | 6edc21e | web 53/324；site 3/9；console-ui 2/6   |
| R-22 | Python service-kit 试点（connector+knowledge） | L    | 中   | audit-python | ✅ done | d3e1510 | main 88→41 / 101→59；health 契约不变   |
| R-23 | service_testkit（零网络 conftest）             | M    | 中   | audit-python | ✅ done | 34269b5 | 6 服务 conftest 收敛；pytest 724       |
| R-24 | alembic_kit + 迁移 smoke                       | M    | 中   | audit-ops    | ✅ done | e060789 | env.py 144→37；5 服务 check 全绿       |
| R-25 | 收编 Fernet/Limiter/内部密钥头                 | S    | 低   | audit-python | ✅ done | 5b1475c | 重复各 1 份；字面量 0                  |
| R-26 | design 令牌单一来源 + Dialog/Button + brand    | M    | 中   | w1-api       | ✅ done | 9aa2ac0 | 入口 292539B；CSS 76346B；已知视觉差异 |

配套修复（非原计划，波次内出现）：06a9bc8、c9d6776、4c131cd（CI 三级修复）、26fa65b（Node 22.22.2）、fb2f10c（SECRET_HEADER）、9a50c22（模型/迁移漂移）、c86028b + 9a50c22（isort）、02c3c36 + dd640e2 + 6db805a（desktop 跨平台用例）。

### W2 出口检查

- [x] 门禁实测：lint 16/16、web 53/324、desktop 59、design 100、console-ui 6、site 9、pytest 724、docs 0 失败、build 3 项目
- [x] 指标全部达成（详见 docs/refactoring/verification/003-w2.md §2）
- [x] CI 7 job 全绿（run 40116313086 / dd640e2），首次真正跑通（此前 ci.yml 模板表达式损坏）
- [x] 回滚演练 1 次（git apply -R --check，R-21 提交）
- [x] 复盘写入 docs/refactoring/retro/w2.md；playbook v1.4 记录 5 条流程改进

## 遗留项（跟踪）

| 项                                                                   | 来源      | 跟踪                                                                     |
| -------------------------------------------------------------------- | --------- | ------------------------------------------------------------------------ |
| dependabot 两个 PR 分支（b6b2dc6 / 33d7ec0）CI 红：分支上是旧 ci.yml | CI 修复   | 重基即可恢复；合并前确认 actions 版本与 Node 22 兼容                     |
| AO / account / knowledge 的 migrations/env.py 未接 alembic_kit       | R-24 偏差 | W3 顺手项（78/70/71 行）                                                 |
| AO conftest（420 行 / 386 例）未接 service_testkit                   | R-23 范围 | W3；AO 的 SKILL_PYTHON_IMAGE / MCP_STDIO_PYTHON_IMAGE 仍受本地 .env 影响 |
| workspace-service test_sandbox_lifecycle 既有环境失败                | W0        | 本机 msb rootfs 缓存问题，非重构引入                                     |
| 6 类 @nxlv/python executor 无 env 透传                               | R-01 降级 | 上游支持后回补；gate/CI 已兜底                                           |
| common:build（uv export）在无 UV_CACHE_DIR 时失败                    | R-01      | gate/CI 均先导出，非阻塞                                                 |
| ROADMAP：local-dev/README 里的 Node 版本口径已同步为 22.22.2         | W2        | 已完成                                                                   |
| R-26 Button/Dialog 与旧控制台样式的视觉差异（34px/32rem 等）         | R-26 偏差 | W4 发布前人工核对截图                                                    |
| vitest 子配置物理收敛（本轮仅文档化）                                | R-21 偏差 | 下波次                                                                   |

## W3（待立项 · 预排）

- 范围：R-27 ~ R-33（docs/refactoring-playbook.md §10.2 W3 表）
- 门禁（§10.3 W3）：全仓 lint/typecheck/test + AO 全量测试 + 迁移检查 + 黄金 run；指标：最大 Python 文件 ≤500、graph 无反向依赖、覆盖率阈值生效
- 复盘重点：高风险拆分的回滚演练
- 建议顺序：R-27（graph/nodes.py 601）+ R-28（services/runs.py 1100）同批同人；R-29（API 层 mcp.py 792 / conversations.py 682 / skill_packages.py 624 / skill_runner.py 666）独立并行；R-30..R-33 质量 ratchet 收尾

| ID   | 重构项（详见 playbook §10.2）             | 规模 | 风险 | 依赖         | 状态   |
| ---- | ----------------------------------------- | ---- | ---- | ------------ | ------ |
| R-27 | 拆 graph/nodes.py（601 行）与反向依赖     | L    | 高   | R-23/R-24 ✅ | 待立项 |
| R-28 | 拆 services/runs.py（1100 行）            | L    | 高   | R-27         | 待立项 |
| R-29 | API 层拆分（mcp/conversations/skill\_\*） | M    | 中   | 无           | 待立项 |
| R-30 | Python 覆盖率阈值与 ratchet               | M    | 低   | 无           | 待立项 |
| R-31 | 前端覆盖率阈值与 ratchet                  | M    | 低   | 无           | 待立项 |
| R-32 | 重复代码与死代码清理（jscpd/knip 或等价） | M    | 低   | 无           | 待立项 |
| R-33 | 性能预算与 bundle 分析入 CI               | S    | 低   | 无           | 待立项 |

## 波次总览

| 波次 | 主题                                                   | 状态                  |
| ---- | ------------------------------------------------------ | --------------------- |
| W0   | 门禁与真源（R-01..R-07）                               | ✅ 收口（2026-10-24） |
| W1   | 前端基座与工具链（R-08..R-16）                         | ✅ 收口（2026-10-24） |
| W2   | 页面拆分 + 跨端共享 + service-kit 试点（R-17..R-26）   | ✅ 收口（2026-10-25） |
| W3   | agent-orchestrator 深水区 + 质量 ratchet（R-27..R-33） | 未开始（待立项）      |
| W4   | 发布与运维（R-34..R-38）                               | 未开始                |
