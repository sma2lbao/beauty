# 对比其他 agent harness：还差什么（2026-10 结论版）

> 本文是「对比其他 agent harness 还有什么需要做的」的总答复。证据分三份分册，本文只给结论与排期：
>
> - [01-repo-inventory.md](./01-repo-inventory.md)：beauty-moon **现状**逐项取证（工具/审批/循环/队列/上下文/扩展/交互面/平台，300+ 行，每条带 file:line）
> - [02a-claude-codex.md](./02a-claude-codex.md)、[02b-oss-harnesses.md](./02b-oss-harnesses.md)：Claude Code / Codex 与 OpenHands / Cline / Aider / Gemini CLI / Goose 的机制横评
> - [03-runtime-platform.md](./03-runtime-platform.md)：沙箱、多 agent 编排、记忆、生态、可观测、安全、SaaS 平台要素（S/M/L 与既定里程碑对齐）
>
> 旧文档 [feature-gap.md](../archive/2026-10/feature-gap.md)（已归档）只对比 DeepSeek Harness 一家；本文把参照面扩到整个 agent harness 领域，**结论以代码证据为准**，公开资料标注「待核实」的不作为决策依据。
> 判定口径：✅ 已具备／⚠️ 部分或「有壳无实」／❌ 缺失；规模 S（≤2 天）/ M（3–5 天）/ L（1–2 周+）。

> **状态注记（2026-10-25 复核）**：本文是 2026-10-18 的快照，**多数「缺失/有壳无实」已落地**（P0 七项、沙箱 P1–P3、stdio MCP、Sentry/OTel/metrics、CI/Docker、技能包、黄金 run、通知等）。逐条校正见 [06-refresh-2026-10-25.md](./06-refresh-2026-10-25.md)（已过时/仍成立/写错三张表）；引用本文结论前先读 06。

---

## 0. 一句话结论

beauty-moon 现在的状态是：**「工具 → 审批 → 执行 → 事件 → SSE → UI」这条单 run 闭环已经跑通，而且工程纪律在同类项目里偏高**
（仓储级租户隔离、事务性 outbox、事件溯源 + 断线续传、测试零网络；TODO 仅剩 2 处，见 06 §2.3 #12）。
但从「闭源 demo 能跑」到「多租户 agent harness 平台」，还差的不是某个大功能，而是**四层地基**：

| #   | 地基                            | 一句话                             | 关键缺口                                                                                                                                                                           |
| --- | ------------------------------- | ---------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A   | **可信度**（不塌、能兜、能续）  | agent 的行为要可预测、失败要可恢复 | 上下文无压缩/无 token 预算、审批只有布尔开关、无 LLM 重试/fallback、非 PG 环境重启丢图状态、无执行沙箱                                                                             |
| B   | **平台化**（能收费、能负责）    | 多租户要能量、能限、能查           | usage 无生产者、quota 无读取点、无 tracing/metrics、无 CI、无用量级限流                                                                                                            |
| C   | **扩展性**（能长、能接）        | 定制不应要求 fork                  | 无 hooks、Skill manifest 多数字段不生效（上传包已支持）、无对外 SDK、MCP 已补 stdio；sampling/roots/logging 已被 2026-07-28 规范弃用、不应补，余 elicitation/tasks/server-discover |
| D   | **产品力**（agent-native 交互） | 用户要看得懂、控得住               | 无 Todo/Goal、无文件树、无终端、无 @ 引用、无代码高亮、无模型选择（**通知已完成前端站内版**）                                                                                      |

**2026-10-25 复核：四层地基的已补/剩余**（逐条证据见 06 §2.1/§3）

| 地基     | 已补                                                                       | 剩余                                                                       |
| -------- | -------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| A 可信度 | 沙箱 P1–P3、审批策略表、工具轮次 5、PG checkpointer                        | 上下文压缩、checkpoint 回退/续跑产品面、run cancel/超时、LLM 重试/fallback |
| B 平台化 | usage 生产者、quota 读取、OTel+Sentry+metrics、jobs SKIP LOCKED、CI/Docker | 成本归因、对话配额闸、outbox claim 锁、限流 Redis 化                       |
| C 扩展性 | stdio MCP、技能包上传、公开 MCP server                                     | hooks、子代理、SDK/Webhook、SKILL.md 渐进加载、MCP 新协议对齐              |
| D 产品力 | 通知、计划编辑、Diff/产物预览、MCP/Skills 页                               | Todo/文件树/终端/@引用/代码高亮、模型选择                                  |

---

## 1. 横向对照：和成熟 harness 比，差距落在哪

> 行=能力域（按 01/02/03 三册收敛），列=beauty-moon 现状与业界代表做法。

| 能力域                 | beauty-moon                                                                                                          | 业界代表做法（来源）                                                                                                                                                                                              | 差距性质                                       |
| ---------------------- | -------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------- |
| **权限/安全模型**      | ⚠️ 仅 `requires_confirmation: bool`；MCP 工具一刀切强制确认；审批无超时                                              | 分层策略（allow/ask/deny）+ 执行边界（沙箱可写/网络），越界才问人（[Claude Code 权限](https://code.claude.com/docs/en/iam)、[Codex 安全](https://developers.openai.com/codex/security)）；hooks 做确定性拦截      | **机制代差**：还停在「逐个点确认」             |
| **执行隔离**           | ❌ 无沙箱；`code_tools` 直接读写服务进程可见目录；无 shell                                                           | isolated runtime 作为一等资源（[OpenHands](https://docs.openhands.dev/openhands/usage/agent-canvas/architecture)）；托管 sandbox E2B/Daytona/Fly 按需计费（[定价横评](https://fly.io/learn/ai-sandbox-pricing/)） | **能力缺位**：coding 类交付被卡住              |
| **上下文管理**         | ❌ 20 条 × 2000 字符硬截断；无 token 计数（网关 usage 被丢弃）；无记忆                                               | 自动压缩 + 约定文件（AGENTS.md / CLAUDE.md / GEMINI.md 已成共识）；Aider 用 repo map 结构化选上下文                                                                                                               | **机制代差**：长任务必然失控                   |
| **循环控制**           | ⚠️ 工具轮次上限 3、返工 3；无 LLM 重试/fallback；`planning/running` 状态从未赋值                                     | 主流 harness 轮次可配置、失败重试与降级为基线；状态由事件推导                                                                                                                                                     | **打磨缺位**                                   |
| **后台任务**           | ⚠️ jobs 表 + lease + 退避 ✅，但无取消/优先级/定时；多实例不安全（未用 SKIP LOCKED）                                 | 后台任务面板、可取消、可观察（[Claude Code 后台任务](https://code.claude.com/docs/en)）                                                                                                                           | **需要收敛到「可托管」**                       |
| **子代理 / 多 agent**  | ❌ 全仓无 subagent                                                                                                   | Task 子代理（独立上下文）/ handoff（[Agents SDK](https://openai.github.io/openai-agents-python/)）/ scoped agent 委派（OpenHands）                                                                                | **可延后**：先用 parent_run_id 子 run 等价替代 |
| **扩展点**             | ⚠️ Skills（仓库文件，permissions/triggers 等字段不生效）、MCP client/server ✅、无 hooks/插件/SDK                    | Skills 以 SKILL.md 打包按需加载（[Anthropic Skills](https://www.anthropic.com/news/skills)）；hooks 拦截工具调用（[Claude Code hooks](https://code.claude.com/docs/en/hooks)）                                    | **平台化关键**：客户定制只能 fork              |
| **可观测 / 评测**      | ❌ 仅 structlog；无 traceId/metrics/LLM 观测；无 eval                                                                | trace→span→generation 模型（[Langfuse](https://langfuse.com/docs)）；OTel GenAI semconv（[spec](https://opentelemetry.io/docs/specs/semconv/gen-ai/)）                                                            | **平台化关键**：线上问题不可定位               |
| **计量 / 配额 / 计费** | ❌ 表与端点齐备但**无生产者**；quota 全仓无读取点；BYOK 只存不用                                                     | usage ledger + 计划配额 + 超限 402 为 SaaS 基线                                                                                                                                                                   | **平台化最关键的一处断点**                     |
| **交互面**             | ⚠️ Markdown/Diff/产物预览/run 看板/计划编辑/通知中心 ✅；缺 Todo、文件树、终端、@引用、代码高亮、模型选择、i18n/主题 | agent-native 控件（todo、plan、终端、文件树）已成标配                                                                                                                                                             | **产品力缺口**                                 |
| **对外接入**           | ✅ 公开 MCP server + OAuth 2.1 + API Key；❌ 无对外 SDK/公开 REST 文档                                               | MCP 已成事实标准（[规范](https://modelcontextprotocol.io/)）                                                                                                                                                      | 已领先半步，缺 SDK 收尾                        |

---

## 2. 需要做的事（按优先级）

### P0 · 不补则「平台不可信」（建议全部放进 M0/M1）

| #    | 事项                                                                                                   | 为什么现在做                                                  | 规模 | 证据                                            |
| ---- | ------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------- | ---- | ----------------------------------------------- |
| P0-1 | **usage 生产者闭环**：llm-gateway 返回的 usage → `usage_events` → run 级汇总                           | 计费、配额、成本归因、评测的共同底座；现在 usage 页恒空       | M    | `services/llm.py:125-133` 丢弃 usage；`01` §6#3 |
| P0-2 | **quota 准入判断**（建 run / 调模型前拒绝或降级）                                                      | `organizations.quota` 全仓只有写没有读                        | S    | `01` §6#2                                       |
| P0-3 | **token 计量与上下文预算**                                                                             | 没有预算就不能上压缩；长会话/长工具输出必然出事故             | S-M  | `01` §2、§8 H1                                  |
| P0-4 | **审批策略引擎**：per-tenant / per-tool / per-arg 的 auto/confirm/deny，替换布尔开关；审批超时自动拒绝 | 全量确认会把 agent 退化成人工点击机；MCP 工具现在全部强制确认 | M    | `01` §1.2、§6；`03` §7                          |
| P0-5 | **traceId + Sentry**（BFF → orchestrator → gateway 贯穿）                                              | 7 个服务无 trace，线上排障无从下手                            | S    | `01` §5、§8 H4                                  |
| P0-6 | **多实例 job 领取**（PG `FOR UPDATE SKIP LOCKED`）                                                     | 现在水平扩容会重复消费；扩容前必须先修                        | S    | `01` §1.5、§6#8                                 |
| P0-7 | **LLM 重试 / fallback / 429 退避**                                                                     | 上游抖动直接变用户可见 502，长 run 中途失败即整跑重来         | S    | `01` §8 H5                                      |

### P1 · M1/M2 内补「能力与体验」

| #     | 事项                                                                                                                                                   | 说明                                                                                      | 规模 |
| ----- | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------- | ---- |
| P1-1  | **断点续跑**                                                                                                                                           | 非 PG 环境 InMemorySaver 重启即丢；补「从 checkpoint 恢复」API                            | M    |
| P1-2  | **压缩做成事件投影**                                                                                                                                   | `context.compacted.v1` 摘要事件，可回放、可评测、可 A/B（借鉴 Aider repo map + 两层记忆） | M    |
| P1-3  | **hooks**（pre/post tool、run_start/end）                                                                                                              | 策略引擎与审计的落地载体；也是「客户不改代码就能定制」的第一步                            | M    |
| P1-4  | **Skills DB 化**                                                                                                                                       | 租户级技能（版本/启停/工具白名单/交付物），让 manifest 的 permissions 真正参与裁决        | M    |
| P1-5  | **子 run**（`parent_run_id`）                                                                                                                          | 等价于 Task 子代理，复用现有状态机与事件流，不引入新框架                                  | M    |
| P1-6  | **沙箱 spike + provider 抽象**                                                                                                                         | 只读 `code_tools` 先产品化；E2B 与 Fly 二选一，一次 run 一个 sandbox，接口抽象避免绑死    | S+M  |
| P1-7  | **黄金 run 回归**                                                                                                                                      | 20 条固定任务的离线回归，作为子代理/压缩/策略改动的验收门禁                               | M    |
| P1-8  | **Redis 限流**                                                                                                                                         | compose 已起 Redis 但从未连接；改三级（租户/IP/Key）并保留进程内降级                      | M    |
| P1-9  | **交互面第一梯队**：Todo/Goal、文件树、代码高亮（~~通知~~已完成前端站内版）                                                                            | 直接决定「像不像一个 agent 产品」                                                         | S+M  |
| P1-9b | ~~**通知服务端化**：SSE 推送~~（已完成：`/api/v1/notifications/stream`，见 `05-notifications.md`）；剩余：浏览器桌面通知（Notification API）、通知偏好 | 服务端推送已通，通知不再只依赖页面内轮询                                                  | S    |
| P1-10 | **模型选择 + per-run model 参数**                                                                                                                      | 现在只能展示默认模型不能选；顺带打通 BYOK 调用链                                          | M    |

### P2 · 暂缓（写明触发条件，避免「以为要做」

| 事项                                    | 触发条件                                                                | 依据    |
| --------------------------------------- | ----------------------------------------------------------------------- | ------- |
| Temporal / Inngest 持久工作流           | 出现大量「跨天等待外部回调」，且 PG checkpointer + 多实例 worker 已稳定 | `03` §2 |
| 长期记忆服务（mem0 / Zep / Letta）      | 租户级约定文件不够用、且数据驻留方案明确                                | `03` §3 |
| 浏览器 / computer use                   | 沙箱与 egress 白名单落地之后，且只做只读抓取                            | `03` §6 |
| Skill / 插件市场、SDK 生态              | 有外部开发者真实需求                                                    | `03` §4 |
| Stripe 自助支付、企业 SSO/RBAC/审计导出 | 从邀请制转向自助注册                                                    | `03` §8 |
| 通用多 agent 任务板/消息总线            | 不计划做（SaaS 客户价值 < 单 run 可靠性）                               | `03` §2 |

---

## 3. 顺带要修的「有壳无实」（低成本高收益）

`01` §6 列出了 12 处「字段/表/端点在、运行时没有消费方」。其中**前 4 条必须在 P0 阶段一并解决**，否则会持续给用户错误预期：

1. **BYOK**：`core/byok.py:1` 自己写着「只加密存储,暂不参与调用」——用户以为切了自己的 key，实际仍烧平台额度。
2. **organizations.quota**：无读取点，超额不拒绝、不提示。
3. **usage_events**：无生产者，usage 页恒空。
4. **run 状态 `planning` / `running`**：枚举有、运行时从不赋值，前端判断语义失真。

其余 8 条（Skill manifest 字段、终端面板命名、限流 Redis、PG SKIP LOCKED、MCP 非内联产物、connector 多 provider、模型选择、BYOK 加密密钥占位）已分别收敛进上文 P0/P1。

---

## 4. 与既定里程碑的映射

| 里程碑 | 本结论新增/收敛的事项                                    |
| ------ | -------------------------------------------------------- |
| **M0** | P0-1 ~ P0-7；「有壳无实」#1-#4 修复                      |
| **M1** | P1-1 ~ P1-8、P1-10；沙箱决策（原 M1-00）与 provider 抽象 |
| **M2** | P1-9 交互面补齐；压缩投影与子 run 的体验化；记忆方案决策 |
| **M3** | 对外 SDK / 公开 REST 收尾；Skill DB 化的分发面           |
| **M4** | 多 agent 任务板（若届时仍有需求）；市场与生态            |

---

## 5. 证据强度声明

- `01` 中所有 ✅/⚠️/❌ 均可在仓库内复核（file:line）；**未确认**项已显式标注。
- `02a/02b/03` 中的产品事实来自公开资料检索，**仅取标题与摘要**，未核实的细节统一写在对应文档的「待核实」块（如各家压缩阈值、权限模式精确档位、Codex 沙箱实现、厂商费率时效）。
- 本文的排期建议不依赖任何「待核实」项；若后续补齐资料与之冲突，以该分册的新结论为准。
