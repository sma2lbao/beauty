# 重构路线图

- 来源：docs/refactoring-playbook.md 第 10 节
- 当前波次：W0–W4 全部收口（2026-10-25），转入维护窗口；最近更新：2026-10-25 10:30 +08:00
- 历史：W0 见 docs/refactoring/retro/w0.md，W1 见 docs/refactoring/retro/w1.md 与 docs/refactoring/verification/002-w1.md，W2 见 docs/refactoring/retro/w2.md 与 docs/refactoring/verification/003-w2.md，W3 见 docs/refactoring/retro/w3.md 与 docs/refactoring/verification/004-w3.md

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

## W3（已收口 · 7 项拆 8 项 + 5 个配套修复）

- 立项：0e2c41e（RFC-004 + baseline「W3 基线」+ roadmap 进行中）
- 出口门禁（§10.3 W3，本波口径）：全仓 lint 16/16 ✅；TS 5 项目 498 例 ✅；Python 10 项目 727 例 ✅；黄金 run 9/9 ✅；docs 门禁 31 文件 0 失败 ✅；服务源码最大 690 行 ✅（W4 收到 ≤500）；graph 反向依赖 0 ✅；覆盖率阈值 10 Python + 6 TS 单元生效 ✅

| ID    | 重构项                                   | 规模 | 风险 | 实施者       | 任务    | 状态    | 提交              |
| ----- | ---------------------------------------- | ---- | ---- | ------------ | ------- | ------- | ----------------- |
| R-27  | graph 去反向依赖 + delta 显式注入        | L    | 高   | audit-python | task-22 | ✅ done | b386fc6           |
| R-28  | 拆 services/runs.py（1149 行）           | L    | 高   | w1-api       | task-23 | ✅ done | 7c01d81           |
| R-29  | api 瘦身 + AppContainer + 分域           | L    | 高   | w1-api       | task-24 | ✅ done | 1c78bc9 + 1737a86 |
| R-30  | common 瘦身（documents 移出 + 依赖隔离） | M    | 中   | lead         | task-25 | ✅ done | aa8d101 + c59e8c9 |
| R-31a | Python 覆盖率 ratchet                    | M    | 中   | w0-ci        | task-26 | ✅ done | feef0d6           |
| R-31b | TS 覆盖率 ratchet                        | M    | 中   | audit-ts     | task-27 | ✅ done | c8d1c66 + 8b1924d |
| R-32  | pytest-socket 零网络硬门禁               | M    | 中   | w0-ci        | task-28 | ✅ done | c33d520           |
| R-33  | 黄金 run 脚本                            | M    | 中   | audit-ops    | task-29 | ✅ done | c649737           |

配套修复（非原计划，波次内出现）：7884737（CI 覆盖率接线）、7833bbf（gate 按项目收集覆盖率）、b78ef8c（AO 审批策略连接泄漏）、c8805ab（用量上报 fail-open）、8ff7545（web 懒加载断言超时）。

偏差登记（RFC-004 §10）：D1 最大 Python 文件分两步（W3 ≤700 / W4 ≤500）；D2 R-30 只做 documents 移出与依赖隔离，metrics 域指标真实拆分顺延 W4；D3 R-32 与 R-33 顺序对调（黄金 run 先立，零网络最后开）；D4 R-31 拆 a/b；D5 cov-fail-under 比较 combined 而非 line；D6 documents.py 双职责；D7 run_execution 562 不拆；D8 golden_run.py 808 行为脚本例外；D9 黄金 run 落目录；D10/D11 顺带修连接泄漏与上报 fail-open。

### W3 出口检查

- [x] 门禁实测：lint 16/16、web 53/324、desktop 59、design 100、console-ui 6、site 9、pytest 727、golden 9/9、docs 0 失败、build chunk 292539B
- [x] 指标全部达成（详见 docs/refactoring/verification/004-w3.md §2，17 行全 ✅）
- [x] 回滚演练：R-30 用 git diff > patch + git apply -R --check（共享工作树禁 stash/reset --hard）
- [x] 复盘写入 docs/refactoring/retro/w3.md；playbook v1.5 记录 4 条流程改进
- [x] CI 全绿（run 40149176437 / c94fc1b：7 组 27 job 全部 success）

## 遗留项（跟踪）

W4 承接的部分已折进 RFC-005：R-39（体量 ≤500 / 测试文件拆分）、R-40（env.py×3 + AO conftest + metrics 归属）；其余继续跟踪。

| 项                                                                                               | 来源      | 跟踪                                                                     |
| ------------------------------------------------------------------------------------------------ | --------- | ------------------------------------------------------------------------ |
| dependabot 两个 PR 分支（b6b2dc6 / 33d7ec0）CI 红：分支上是旧 ci.yml                             | CI 修复   | 重基即可恢复；合并前确认 actions 版本与 Node 22 兼容                     |
| AO / account / knowledge 的 migrations/env.py 未接 alembic_kit                                   | R-24 偏差 | W4（78/70/71 行）                                                        |
| AO conftest（420 行 / 394 例）未接 service_testkit                                               | R-23 范围 | W4；AO 的 SKILL_PYTHON_IMAGE / MCP_STDIO_PYTHON_IMAGE 仍受本地 .env 影响 |
| workspace-service test_sandbox_lifecycle 既有环境失败                                            | W0        | 本机 msb rootfs 缓存问题，非重构引入                                     |
| 6 类 @nxlv/python executor 无 env 透传                                                           | R-01 降级 | 上游支持后回补；gate/CI 已兜底                                           |
| common:build（uv export）在无 UV_CACHE_DIR 时失败                                                | R-01      | gate/CI 均先导出，非阻塞                                                 |
| R-26 Button/Dialog 与旧控制台样式的视觉差异（34px/32rem 等）                                     | R-26 偏差 | W4 发布前人工核对截图                                                    |
| vitest 子配置物理收敛（本轮仅文档化）                                                            | R-21 偏差 | W4 评估                                                                  |
| 5 个 >500 行测试文件（AO test_skill_runner 995 / test_mcp_stdio 774 等）                         | W3 结构   | W4 按用例域拆                                                            |
| llm-gateway 并行跑 litellm rust_bridge 循环导入 flake                                            | R-31a     | 单跑绿；W4 评估串行或隔离                                                |
| 分支覆盖率（branch%）无阈值                                                                      | R-31a     | W4 评估                                                                  |
| metrics 域指标归属（common.metrics 被 AO 与 llm-gateway 共用）                                   | D2        | W4 拆分                                                                  |
| web 剩余 4 个 >400 行源文件（workspace-switcher 543 / message-list 528 / runs 448 / skills 432） | W2 结构   | W4 评估                                                                  |
| alembic_kit 落地后 3 处 env.py 仍未接（AO/account/knowledge）                                    | R-24      | W4                                                                       |

## W4（已收口 · 7 项 R-34..R-40）

- 立项：78c91ef（RFC-005 + baseline「W4 基线」+ roadmap 进行中）；偏差登记 5706183
- 目标：发布可重复、安全扫描进 CI、容器化可跑、探针一致、体量 ≤500、W3 接线收口、工作区卫生有检查
- 验证：docs/refactoring/verification/005-w4.md（14 行指标全 ✅、D1–D9 偏差、11 个提交清单）；复盘：docs/refactoring/retro/w4.md

| ID    | 重构项                                            | 规模 | 风险 | 实施者       | 任务    | 状态    | 提交    |
| ----- | ------------------------------------------------- | ---- | ---- | ------------ | ------- | ------- | ------- |
| R-34  | 发布流程：单一版本 0.1.0 + nx release + CHANGELOG | M    | 低   | w0-ci        | task-33 | ✅ done | a1f69b6 |
| R-35  | 依赖更新与安全扫描（dependabot + audit 入 CI）    | S    | 低   | w0-ci        | task-33 | ✅ done | a1f69b6 |
| R-36  | 容器化：2 Dockerfile + 全栈 compose + README      | L    | 中   | audit-ops    | task-38 | ✅ done | 9f35d79 |
| R-37  | 探针统一（bff /readyz + nginx 探针/TLS）          | M    | 低   | w0-gate      | task-32 | ✅ done | edecdd2 |
| R-38  | 工作区卫生（.gitignore + check-workspace.sh）     | S    | 低   | w0-docs      | task-34 | ✅ done | 0678396 |
| R-39a | 源文件体量 ≤500（10 个文件）                      | L    | 中   | audit-python | task-35 | ✅ done | 5bb665b |
| R-39b | 测试文件体量 ≤500（6 个文件）                     | M    | 中   | audit-ts     | task-36 | ✅ done | 03f0c11 |
| R-40  | 接线收口（env.py×3 + AO conftest + metrics 归属） | M    | 中   | w1-api       | task-37 | ✅ done | 546a976 |

配套提交：82bdeb5（CHANGELOG 0.1.0 基线）、3db0f08（根 .dockerignore）；a1f69b6 内同时完成 lead 的 ci.yml 收口接线（docs-check 增 workspace 卫生、新增 docker-build job、job 编号改 x/9）。

### W4 出口检查（勾选前须有实测输出）

- [x] 版本 18 处全 0.1.0，check-versions.sh EXIT 0，nx release dry-run EXIT 0（a1f69b6 后实测）
- [x] security-audit job 已接线；npm 生产依赖阻断通过（本地 `npm audit --omit=dev` → 0 vulnerabilities）；允许清单 4 条未过期
- [x] 2 个 Dockerfile（77 / 62 行）+ compose config EXIT 0（11 服务）+ CI docker-build job；deploy README 241 行覆盖 TLS/迁移/回滚（本机无 daemon，构建正确性由 CI 验，D2）
- [x] /healthz /readyz 7/7 且契约一致（bff 28 passed）；nginx 2 个探针 location（:38 / :46）
- [x] 最大 Python 源文件 482 ≤500、最大测试文件 498 ≤500；pytest 737 ≥727、vitest 498 ≥498、golden 9/9
- [x] check-workspace.sh EXIT 0 且入 gate + CI；5/5 env.py 接 alembic_kit；AO conftest 接 testkit（411 行，398 例不减）
- [x] 全仓 lint 16/16、docs 36 文件 0 失败、入口 chunk 292539B ≤336KB、CI 9 组/31 matrix job（结果见 verification §1 与 §5）
- [x] CI 最新 run 40155457226（8dd3955）九组 31/31 全绿，run 号已回填 verification §1/§5.3
- [x] tag v0.1.0 已打（`git tag --list 'v*'` 非空），release commit 回填 verification §5.3
- [x] verification/005-w4.md 所有字段均已填实（收口 DoD）

## 波次总览

| 波次 | 主题                                                   | 状态                  |
| ---- | ------------------------------------------------------ | --------------------- |
| W0   | 门禁与真源（R-01..R-07）                               | ✅ 收口（2026-10-24） |
| W1   | 前端基座与工具链（R-08..R-16）                         | ✅ 收口（2026-10-24） |
| W2   | 页面拆分 + 跨端共享 + service-kit 试点（R-17..R-26）   | ✅ 收口（2026-10-25） |
| W3   | agent-orchestrator 深水区 + 质量 ratchet（R-27..R-33） | ✅ 收口（2026-10-25） |
| W4   | 发布与运维（R-34..R-40）+ W3 遗留                      | ✅ 收口（2026-10-25） |

重构主线（W0–W4，38 个 R 项）已全部收口，转入维护窗口：dependabot 分组 PR 观察、安全允许清单 2026-12-31 到期复核、CI 新增 job 稳定性观察。
