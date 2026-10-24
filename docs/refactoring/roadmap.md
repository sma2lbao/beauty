# 重构路线图（W0 已收口）

- 来源：docs/refactoring-playbook.md 第 10 节
- 当前波次：W0 完成（2026-10-24），W1 待立项；最近更新：2026-10-24 21:00 +08:00

| ID | 波次 | 范围 | 规模 | 风险 | 依赖 | owner | 状态 | 提交 | 出口证据 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R-01 | W0 | Python target 环境固化 | S | 低 | 无 | w0-gate | ✅ done | (本轮合入) | gate 自测 common EXIT 0 |
| R-02 | W0 | 根 scripts/engines/.nvmrc | S | 低 | 无 | lead | ✅ done | (本轮合入) | npm run gate 可用；锁文件已重算 |
| R-03 | W0 | 最小 CI（6 job） | L | 中 | R-01 | w0-ci | ✅ done（真跑待 push 验证） | (本轮合入) | js-yaml 解析 6 job；命令对照表见 ci.yml 注释 |
| R-04 | W0 | Python implicitDependencies | S | 低 | 无 | w0-gate | ✅ done | (本轮合入) | nx graph 7 服务边出现（19 边） |
| R-05 | W0 | 文档单一真源 | M | 低 | 无 | w0-docs | ✅ done | (本轮合入) | 归档 4/4；断链 0；过时事实 0 |
| R-06 | W0 | PR 模板+提交规范 | S | 低 | R-03 | w0-ci | ✅ done | (本轮合入) | 模板 19 字段 |
| R-07 | W0 | 门禁脚本 | M | 低 | 无 | w0-gate | ✅ done | (本轮合入) | 双项目自测 EXIT 0 |

## W0 出口检查

- [x] playbook §10.3 W0 门禁：全仓 lint（15/15）+ 受影响项目 typecheck/test（Python 抽 3 + TS 全 3 全绿）+ scripts/refactor-gate.sh 自测（双项目 EXIT 0）
- [x] 指标对比达标（pytest 697 不减少；vitest 445 实测口径；lint 0 新增）
- [x] 复盘写入 docs/refactoring/retro/w0.md
- [x] W1 任务可立项（R-08..R-16 无阻塞）

## 遗留项（跟踪）

| 项 | 来源 | 跟踪 |
| --- | --- | --- |
| CI 首次真实运行（push 后）验证 job 耗时与 affected 退化行为 | R-03 | W1 立项前 |
| workspace-service test_sandbox_lifecycle 既有环境失败（本机 msb rootfs 缓存） | gate --affected 发现 | R-15 清理清单 |
| @nxlv/python 6 类 executor（sync/lock/add/update/remove/install、ruff-check/format）schema 无 env | R-01 降级方案 | 上游支持后回补 project.json；gate/CI 已兜底 |
| common:build（uv export）在无 UV_CACHE_DIR 时失败（build executor 不吃 env） | R-01 | gate/CI 均先导出，非阻塞 |
| docs/README.md 的 PR 模板行待 R-06 落地后改回链接 | R-05 | 已由 R-06 落地，链接待 lead 顺手更新 |

## W1（下一波，未立项）

R-08 env 集中、R-09 queryKey registry、R-10 路由拆包、R-11 http 工厂、R-12 SSE 基座、R-13 拆 presentation.ts、R-14 eslint 边界、R-15 dev.sh 健壮性、R-16 文档门禁。依赖关系见 playbook §10.4。

