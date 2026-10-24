# 重构路线图（W0 已收口）

- 来源：docs/refactoring-playbook.md 第 10 节
- 当前波次：W0 完成（2026-10-24），W1 待立项；最近更新：2026-10-24 21:00 +08:00

| ID   | 波次 | 范围                        | 规模 | 风险 | 依赖 | owner   | 状态                        | 提交       | 出口证据                                     |
| ---- | ---- | --------------------------- | ---- | ---- | ---- | ------- | --------------------------- | ---------- | -------------------------------------------- |
| R-01 | W0   | Python target 环境固化      | S    | 低   | 无   | w0-gate | ✅ done                     | (本轮合入) | gate 自测 common EXIT 0                      |
| R-02 | W0   | 根 scripts/engines/.nvmrc   | S    | 低   | 无   | lead    | ✅ done                     | (本轮合入) | npm run gate 可用；锁文件已重算              |
| R-03 | W0   | 最小 CI（6 job）            | L    | 中   | R-01 | w0-ci   | ✅ done（真跑待 push 验证） | (本轮合入) | js-yaml 解析 6 job；命令对照表见 ci.yml 注释 |
| R-04 | W0   | Python implicitDependencies | S    | 低   | 无   | w0-gate | ✅ done                     | (本轮合入) | nx graph 7 服务边出现（19 边）               |
| R-05 | W0   | 文档单一真源                | M    | 低   | 无   | w0-docs | ✅ done                     | (本轮合入) | 归档 4/4；断链 0；过时事实 0                 |
| R-06 | W0   | PR 模板+提交规范            | S    | 低   | R-03 | w0-ci   | ✅ done                     | (本轮合入) | 模板 19 字段                                 |
| R-07 | W0   | 门禁脚本                    | M    | 低   | 无   | w0-gate | ✅ done                     | (本轮合入) | 双项目自测 EXIT 0                            |

## W0 出口检查

- [x] playbook §10.3 W0 门禁：全仓 lint（15/15）+ 受影响项目 typecheck/test（Python 抽 3 + TS 全 3 全绿）+ scripts/refactor-gate.sh 自测（双项目 EXIT 0）
- [x] 指标对比达标（pytest 697 不减少；vitest 445 实测口径；lint 0 新增）
- [x] 复盘写入 docs/refactoring/retro/w0.md
- [x] W1 任务可立项（R-08..R-16 无阻塞）

## 遗留项（跟踪）

| 项                                                                                                                                     | 来源                 | 跟踪                                        |
| -------------------------------------------------------------------------------------------------------------------------------------- | -------------------- | ------------------------------------------- |
| CI 首次真实运行验证：已 push c9b3d63 触发首跑（2026-10-24 21:10），结果待 GitHub Actions 页面确认；affected 基线无变更时退化全量属预期 | R-03                 | 结果出来后回填此行                          |
| workspace-service test_sandbox_lifecycle 既有环境失败（本机 msb rootfs 缓存）                                                          | gate --affected 发现 | R-15 清理清单                               |
| @nxlv/python 6 类 executor（sync/lock/add/update/remove/install、ruff-check/format）schema 无 env                                      | R-01 降级方案        | 上游支持后回补 project.json；gate/CI 已兜底 |
| common:build（uv export）在无 UV_CACHE_DIR 时失败（build executor 不吃 env）                                                           | R-01                 | gate/CI 均先导出，非阻塞                    |
| ~~docs/README.md 的 PR 模板行~~ 已解决：R-06 已落地，lead 已将索引行改为链接                                                           | R-05                 | 完成                                        |

## W1（进行中 · 2026-10-24 立项）

- 立项文档：docs/refactoring/rfc/002-w1.md；基线：docs/refactoring/baseline.md「W1 基线」小节
- 出口门禁（§10.3 W1）：request<T> 定义=0；getReader=1；import.meta.env=0；queryKey 字面量=0；formatTime/formatSize=1；单 PR ≤400 行；bundle 新基线入库

| ID   | 波次 | 范围                                     | 规模 | 风险 | 依赖                  | owner      | 状态           | 提交 | 出口证据                       |
| ---- | ---- | ---------------------------------------- | ---- | ---- | --------------------- | ---------- | -------------- | ---- | ------------------------------ |
| R-08 | W1   | env 集中（14 处 → config/env.ts）        | S    | 低   | R-11/R-12/R-13 合入后 | w1-env     | 🚧 in_progress | —    | import.meta.env=0              |
| R-09 | W1   | queryKey registry（58 字面量 → 工厂）    | S    | 低   | R-13 合入后           | w1-env     | 🚧 in_progress | —    | 字面量=0，缓存失效行为不变     |
| R-10 | W1   | 路由级拆包（15 页 static → lazy）        | S    | 低   | 无                    | lead       | 🚧 in_progress | —    | 入口 chunk < 737954B，新值入库 |
| R-11 | W1   | HTTP client 工厂（10 份 request<T> → 1） | M    | 低   | 无                    | w1-api     | 🚧 in_progress | —    | 本地 request<T>=0，测试全绿    |
| R-12 | W1   | SSE 基座（3 份 reader → lib/sse.ts）     | M    | 中   | R-11                  | w1-api     | 🚧 in_progress | —    | getReader=1，SSE 语义等价      |
| R-13 | W1   | 拆 presentation.ts + 去重 format         | S    | 低   | 无                    | w1-present | 🚧 in_progress | —    | 单文件≤4，format 定义=1        |
| R-14 | W1   | eslint 边界与体量规则                    | S    | 低   | 无                    | w1-tooling | 🚧 in_progress | —    | lint 15/15 绿，命中数记录      |
| R-15 | W1   | dev.sh 健壮性（尽力）                    | M    | 低   | 无                    | w1-tooling | 🚧 in_progress | —    | 4 缺陷修复或记遗留             |
| R-16 | W1   | 文档门禁（prettier+链接检查入 gate）     | S    | 低   | 无                    | w1-tooling | 🚧 in_progress | —    | docs 检查全绿并入 gate         |

## W1 任务板映射

- task-8 = R-14 + R-16（+R-15 尽力）→ w1-tooling
- task-9 = R-11 + R-12 → w1-api
- task-10 = R-13 → w1-present
- task-11 = R-08 + R-09 → w1-env（blocked_by task-9、task-10）
- task-12 = R-10 → lead
