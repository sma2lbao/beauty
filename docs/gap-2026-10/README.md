# 对比 agent harness：差距与待办（2026-10）

> 入口：先读 [00-summary.md](./00-summary.md)（结论与 P0/P1/P2 排期），需要证据再看分册。

| 文件                                                   | 内容                                                                                                  | 状态 |
| ------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- | ---- |
| [00-summary.md](./00-summary.md)                       | **总答复**：四层地基（可信度/平台化/扩展性/产品力）+ P0/P1/P2 待办 + 里程碑映射                       | ✅   |
| [01-repo-inventory.md](./01-repo-inventory.md)         | beauty-moon 现状逐项取证（工具/审批/循环/队列/上下文/扩展/交互面/平台 + 12 处「有壳无实」+ 缺失清单） | ✅   |
| [02a-claude-codex.md](./02a-claude-codex.md)           | Claude Code / OpenAI Codex 横评                                                                       | ✅   |
| [02b-oss-harnesses.md](./02b-oss-harnesses.md)         | OpenHands / Cline / Aider / Gemini CLI / Goose 横评 + 可借鉴结论                                      | ✅   |
| [03-runtime-platform.md](./03-runtime-platform.md)     | 沙箱/多 agent/上下文/生态/评测/浏览器/安全/SaaS 平台要素（含 S/M/L 与优先级表）                       | ✅   |
| [04-sandbox-selfhost.md](./04-sandbox-selfhost.md)     | **沙箱自建专题**：自建=写 provider 层而非内核层；引擎候选与 spike 路径                                | ✅   |
| [05-notifications.md](./05-notifications.md)           | **通知能力实现记录**：前端站内通知中心 + toast + run 状态探测（P1-9 已完成部分）                      | ✅   |
| [06-refresh-2026-10-25.md](./06-refresh-2026-10-25.md) | **2026-10-25 复核刷新**：旧结论逐条校正（已过时/仍成立/写错）+ 能力差距 P0/P1/P2 + 文档纠错清单       | ✅   |

**复核基线**：`01` 的每条结论都带 `file:line`；`02a/02b/03` 的产品事实来自公开检索，未核实项在各文「待核实」块，不作为决策依据。

> **2026-10-25 复核**：`00`/`01`/`02b` 与 `03`/`04`/`05` 的部分结论已被取代，**最新真源是 [06-refresh-2026-10-25.md](./06-refresh-2026-10-25.md)**；本表「状态」列只表示文件已交付，不代表结论仍成立。
