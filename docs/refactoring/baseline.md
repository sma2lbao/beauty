# 基线（W0 · 2026-10-24）

- 采集环境：macOS（本地）；Node v24.14.1 / npm 11.11.1 / Python 3.12（根 .python-version）/ uv（仓库本地缓存 .uv-cache、.uv-python）；Nx 23.2.1
- 采集人 / 时间：重构 Lead（AI）/ 2026-10-24 19:30 +08:00
- 说明：数值均为命令真实输出；标注「审计值」的取自 .cache/refactor-audit 三份报告，本轮未复跑。

| 维度 | 指标 | 采集命令 | 基线值 | 目标 | 备注 |
| --- | --- | --- | --- | --- | --- |
| 结构 | 最大 TS 文件行数 | git ls-files 'apps/**/*.ts(x)' 后 wc -l 排序 | 1313（apps/web/src/pages/chat.tsx） | ≤400（W2 起） | 热点表见 playbook §9.3 |
| 结构 | 最大 Python 文件行数 | find packages services -name '*.py' 排除 tests 后 wc -l | 1100（agent_orchestrator/services/runs.py） | ≤500（W3） | 同上 |
| 结构 | 单目录文件数 | ls apps/web/src/lib | 41 | ≤20 | W2 分层后 |
| 结构 | 循环依赖 | 手工核对 import | 已知 1 处：graph/nodes.py:23-30 ↔ services/runs.py:28 | 0（W3 R-27） | 待工具化 |
| 质量 | pytest 用例数 | cd <项目> && uv run pytest --collect-only -q | 697（103 个测试文件）：common 71 / database 22 / events 26 / AO 386 / llm-gateway 22 / connector 11 / knowledge 23 / account 39 / workspace 74 / bff 23 | 不减少 | 实测 |
| 质量 | vitest 用例数 | npx nx test <项目> -- --run | 445（74 文件）：web 292(45) / desktop 58(10，2 个 vitest 项目) / design 95(19)；早期口径 446 系粗 grep 误差，以本行实测为准 | 不减少 | W0 出口实测（2026-10-24） |
| 质量 | lint / typecheck | npx nx lint/typecheck | design lint EXIT0（3 warning，4.0s）；design typecheck EXIT0（3.1s）；common lint EXIT0（2s，固化缓存后）；common test EXIT0（18.1s） | 0 error | playbook §9.7 |
| 质量 | TODO/FIXME | grep -r 'TODO\|FIXME' 源码 | 5 | 不增加 | |
| 行为 | 零网络测试 | 钉住开关后跑 pytest | 全绿（各服务测试不依赖外部服务） | 全绿 | AGENTS.md 纪律 |
| 性能 | web 构建产物 | npx nx build @beauty/web 后统计 dist | 单 JS chunk 737954B + CSS 77514B；0 处 lazy/Suspense | 只降不升 | 审计值（取自既有 dist），W1 R-10 落地时复测 |
| 规模 | TS/TSX 行数 | git ls-files 后 wc -l | 33319 行（236 文件）+ CSS 2226 行 | — | web 21860/120、desktop 6734/43、design 3216/57、site 1509/16 |
| 规模 | Python 行数 | 同上（排除 tests） | 约 2.82 万行 | — | AO 15878 为最大 |

