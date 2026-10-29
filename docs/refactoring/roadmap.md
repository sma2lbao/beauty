# 重构路线图

- 来源：docs/refactoring-playbook.md 第 10 节
- 当前波次：W6 已收口（2026-10-29）；W0–W5 已收口（2026-10-29）；最近更新：2026-10-29
- 历史：W0 见 docs/refactoring/retro/w0.md，W1 见 docs/refactoring/retro/w1.md 与 docs/refactoring/verification/002-w1.md，W2 见 docs/refactoring/retro/w2.md 与 docs/refactoring/verification/003-w2.md，W3 见 docs/refactoring/retro/w3.md 与 docs/refactoring/verification/004-w3.md，W4 见 docs/refactoring/retro/w4.md 与 docs/refactoring/verification/005-w4.md，W5 见 docs/refactoring/retro/w5.md 与 docs/refactoring/verification/006-w5.md，W6 见 docs/refactoring/retro/w6.md 与 docs/refactoring/verification/007-w6.md

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

W4 承接的部分已折进 RFC-005：R-39（体量 ≤500 / 测试文件拆分）、R-40（env.py×3 + AO conftest + metrics 归属）；W6 承接 web 结构项（已消解，见下表与 verification/007-w6.md）。其余继续跟踪。

| 项                                                                                                   | 来源      | 跟踪                                                                            |
| ---------------------------------------------------------------------------------------------------- | --------- | ------------------------------------------------------------------------------- |
| dependabot 两个 PR 分支（b6b2dc6 / 33d7ec0）CI 红：分支上是旧 ci.yml                                 | CI 修复   | 重基即可恢复；合并前确认 actions 版本与 Node 22 兼容                            |
| AO / account / knowledge 的 migrations/env.py 未接 alembic_kit                                       | R-24 偏差 | W4（78/70/71 行）                                                               |
| AO conftest（420 行 / 394 例）未接 service_testkit                                                   | R-23 范围 | W4；AO 的 SKILL_PYTHON_IMAGE / MCP_STDIO_PYTHON_IMAGE 仍受本地 .env 影响        |
| workspace-service test_sandbox_lifecycle 既有环境失败                                                | W0        | 本机 msb rootfs 缓存问题，非重构引入                                            |
| 6 类 @nxlv/python executor 无 env 透传                                                               | R-01 降级 | 上游支持后回补；gate/CI 已兜底                                                  |
| common:build（uv export）在无 UV_CACHE_DIR 时失败                                                    | R-01      | gate/CI 均先导出，非阻塞                                                        |
| R-26 Button/Dialog 与旧控制台样式的视觉差异（34px/32rem 等）                                         | R-26 偏差 | W4 发布前人工核对截图                                                           |
| vitest 子配置物理收敛（本轮仅文档化）                                                                | R-21 偏差 | W4 评估                                                                         |
| 5 个 >500 行测试文件（AO test_skill_runner 995 / test_mcp_stdio 774 等）                             | W3 结构   | W4 按用例域拆                                                                   |
| llm-gateway 并行跑 litellm rust_bridge 循环导入 flake                                                | R-31a     | 单跑绿；W4 评估串行或隔离                                                       |
| 分支覆盖率（branch%）无阈值                                                                          | R-31a     | W4 评估                                                                         |
| metrics 域指标归属（common.metrics 被 AO 与 llm-gateway 共用）                                       | D2        | W4 拆分                                                                         |
| web 剩余 4 个 >400 行源文件（workspace-switcher 543 / message-list 528 / runs 448 / skills 432）     | W2 结构   | ✅ W6 已消解：非测试源文件 >400 = 0（最大 381），见 verification/007-w6.md §2.1 |
| web 4 个 400~500 行测试文件（runs.test 474 / run-detail.test 449 / mcp.test 433 / sidebar.test 426） | W6 D5     | 后续按用例域拆，或先抽公共 fixture 到 tests/support                             |
| web eslint 体量与分层规则仍为 warn（max-lines 400/500、features↛pages、console 原语↛features）       | R-53      | 当前 0 命中；观察 1–2 个波次后评估升 error                                      |
| components/{brand,layout} 不属业务域，未迁 features/                                                 | W6        | 如需域化另立 RFC                                                                |
| alembic_kit 落地后 3 处 env.py 仍未接（AO/account/knowledge）                                        | R-24      | W4                                                                              |

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

## W5（已收口 · 5 项 R-41..R-45）

- 立项：docs/refactoring/rfc/006-w5.md + baseline「W5 基线」；来源：W4 收口后 33 个功能提交把体量预算打回（15 源 + 10 测试文件 >500 行）+ 5/8 服务未接 service_kit + 6 个死配置键
- 目标：源/测试文件全部 ≤500（`helpers/fs_helper.py` 登记例外 D1）、8/8 服务接入 service_kit、死配置 0 残留、体量 ratchet 入 gate/CI
- 切片（7 片并行，写域文件级互斥）：S1 装配收敛（task-1）、S2a workspace（task-2）、S2b tool-service（task-3）、S2c AO 核心（task-4）、S2d AO 服务层 A（task-5）、S2e AO 服务层 B（task-6）、S2f AO 测试（task-7）
- 收口：13 个提交（c2b1e91..7d35939，158 文件 +16690/−13181）+ 验证报告/复盘；11 项目 pytest 1885 passed / 5 skipped、ruff 11/11、体量 ratchet EXIT 0
- 合并与 CI：PR #20 以 rebase-merge 合入 main（head `3438126`，2026-10-29）；首跑暴露的 3 个既有红项由 PR #21 修复，run 40899448697 与 main run 40900257289 均 34/34 全绿

| ID   | 重构项                                                | 规模 | 风险 | 实施者                              | 任务       | 状态 | 提交                                            |
| ---- | ----------------------------------------------------- | ---- | ---- | ----------------------------------- | ---------- | ---- | ----------------------------------------------- |
| R-41 | 源文件体量 ≤500（14 个，`fs_helper.py` 例外 D1）      | L    | 中   | ws-sandbox/tool-exec/ao-\*/assembly | task-2..6  | ✅   | f51a4d5/a4c27eb/bb25048/0d755a3/443cebd/8d6f96a |
| R-42 | 测试文件体量 ≤500（10 个）                            | L    | 中   | ws-sandbox/tool-exec/ao-tests       | task-2/3/7 | ✅   | f51a4d5/a4c27eb/8f694b3                         |
| R-43 | 装配收敛：5 服务接 service_kit（探针/中间件单一真源） | L    | 中   | assembly                            | task-1     | ✅   | b1531a2/94ee767                                 |
| R-44 | 死配置清理（6 键）                                    | S    | 低   | assembly                            | task-1     | ✅   | 94ee767                                         |
| R-45 | 体量 ratchet（check-python-size.sh）+ 文档收口        | S    | 低   | lead                                | task-40    | ✅   | 4e154b6/7d35939 + 本波文档提交                  |

### W5 出口检查（勾选前须有实测输出）

- [x] `bash scripts/check-python-size.sh` EXIT 0（仅 1 个白名单 WARN：`services/tool-service/helpers/fs_helper.py` 1058，D1）
- [x] 11 个 Python 项目 pytest 全绿且 ≥ 1879 passed / 5 skipped（实测 1885 / 5 skipped）
- [x] 11/11 Python 项目 ruff All checks passed（llm-gateway 的 3 个既有告警在 a001359 修掉，D4）
- [x] 8/8 服务 main.py 走 service_kit；5 个手写装配 main.py 合计 545 ≤ 基线 614（AO 例外 D5）
- [x] 死配置 6 键全仓 0 命中（实际删 7 处，含 AO 同名 `tool_timeout_seconds`）
- [x] docs / workspace / versions 三个门禁脚本 EXIT 0（check-docs 52 文件、check-versions 19 字段、compose config EXIT 0）
- [x] verification/006-w5.md 字段填实；retro/w5.md 复盘落地（CI run 号已回填 §5：PR #20 run 40893337363 → main run 40894815075 → 修复后 PR #21 run 40899448697 全绿）
- [x] CI 首跑暴露的 3 个既有红项（alembic-check tool-service / affected-js / security-audit）已由 PR #21 修复，main 侧 CI 全绿
- [x] 已知既有失败登记：黄金 run 1/9（D2）、ruff format 漂移 9 文件（D3）——均非本波引入，见 verification §4

## W6（apps/web 结构收敛 · ✅ 收口 · 9 项 R-46..R-54）

- 立项：见下文本波「文档提交」（RFC-007 + baseline「W6 基线」+ 本节与波次总览）；编号说明见 RFC-007 §1.0（W5 / RFC-006 已被 services 波次占用，本波 rebase 到 3438126 后改编号）
- 目标：业务域组件归位 features/、非测试源文件 ≤400 行、页面只做组装、formatBytes 单一来源、规则防回流（eslint 探针验证）、文档收口
- 验证：docs/refactoring/verification/007-w6.md（§2 指标全 ✅、D1–D12 偏差、8 个重构提交 + 2 个文档提交清单）
- 执行方式：R-46/R-52/R-53/R-54 由 lead 直接实施；R-47..R-51 由 5 个 fresh-context teammate 并行（任务板 task-1..task-5、write scope 互斥），既有测试文件零改动，收敛后 lead 复跑全量

| ID   | 重构项                                                         | 规模 | 风险 | 实施者           | 任务   | 状态    | 提交    |
| ---- | -------------------------------------------------------------- | ---- | ---- | ---------------- | ------ | ------- | ------- |
| R-46 | 业务域组件从 components/console 归位 features/（41→12 文件）   | L    | 中   | lead             | —      | ✅ done | a3b4a50 |
| R-52 | auth 三页（login/callback/oauth-authorize）迁 features/auth    | S    | 低   | lead             | —      | ✅ done | edbc31b |
| R-47 | runs 拆分（run-detail 728 / timeline 719 / page 466）          | L    | 中   | runs-refactor    | task-1 | ✅ done | 751ae87 |
| R-48 | sandbox 拆分 + formatBytes 去重（712/494 → 37/139）            | L    | 中   | sandbox-refactor | task-2 | ✅ done | 27595f9 |
| R-49 | memory/skills 收口（633/432 → 69/33）                          | M    | 中   | memory-skills    | task-3 | ✅ done | 8f6c857 |
| R-50 | workspace-switcher / message-list 拆分（543/560 → 141/269）    | M    | 中   | workspace-chat   | task-4 | ✅ done | 6c26c4d |
| R-51 | knowledge/outbox/changes/usage 四页收口（内联取数清零）        | M    | 中   | pages-model      | task-5 | ✅ done | ff16f39 |
| R-53 | eslint 体量与分层规则收紧（web 局部）+ runs-api 拆分           | M    | 中   | lead             | —      | ✅ done | 77f3c09 |
| R-54 | 文档收口（verification / baseline / roadmap / retro / AGENTS） | S    | 低   | lead             | —      | ✅ done | 396741e |

### W6 出口检查（勾选前须有实测输出）

- [x] 非测试源文件 >400 行 = 0（最大 381 features/auth/login-page.tsx）；最大测试 474 ≤500；最大页面 112
- [x] components/console 12 文件（4 外壳 + 5 原语 + 3 测试）；features 16 域；components/runs 已清空
- [x] pages 内联 useQuery/useMutation/useQueryClient = 0；formatBytes 定义仅 1 处（lib/presentation/format.ts:164）
- [x] eslint 探针 7 例全符合（命中/豁免/体量边界）；nx lint @beauty/web 0 error（2 条既有 react-refresh warning 属 D2）
- [x] typecheck EXIT 0；vitest 69 files / 493 tests（与基线持平，测试文件零改动）；build 入口 294,671B / CSS 78,127B ≤336KB
- [x] refactor-gate.sh @beauty/web 7/7 步 PASS（lint/typecheck/test/build/docs/workspace/py-size）；check-docs.sh 0 失败
- [x] 8 个重构提交各自独立可 revert（rebase 到 origin/main 后哈希更新）；全程未用 git stash / reset --hard / checkout
- [x] verification/007-w6.md 字段全部填实；baseline「W6 收口实测」与本节提交列回填（R-54 提交后）

| 波次 | 主题                                                   | 状态                  |
| ---- | ------------------------------------------------------ | --------------------- |
| W0   | 门禁与真源（R-01..R-07）                               | ✅ 收口（2026-10-24） |
| W1   | 前端基座与工具链（R-08..R-16）                         | ✅ 收口（2026-10-24） |
| W2   | 页面拆分 + 跨端共享 + service-kit 试点（R-17..R-26）   | ✅ 收口（2026-10-25） |
| W3   | agent-orchestrator 深水区 + 质量 ratchet（R-27..R-33） | ✅ 收口（2026-10-25） |
| W4   | 发布与运维（R-34..R-40）+ W3 遗留                      | ✅ 收口（2026-10-25） |
| W5   | services 装配收敛与体量回补（R-41..R-45）              | ✅ 收口（2026-10-29） |
| W6   | apps/web 结构收敛与体量收口（R-46..R-54）              | ✅ 收口（2026-10-29） |

重构主线（W0–W6，52 个 R 项）已全部收口：W5 为功能提交后的 Python 体量/装配回补（5 项），W6 为 apps/web 结构收敛与体量收口（9 项，含规则防回流）。收口后回到维护窗口：dependabot 分组 PR 观察、安全允许清单 2026-12-31 到期复核、CI 新增 job 稳定性观察、web eslint 规则由 warn 升 error 的观察评估。
