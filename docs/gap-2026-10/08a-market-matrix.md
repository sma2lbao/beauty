# 08a 市场矩阵（2026-10 复盘）：主流开源／事实标准 Agent harness 能力对比

> 撰写：market-researcher ｜ 检索日期 **2026-10-27** ｜ 状态：draft，供 00-summary 下一版引用
> 范围：只写**机制层**（怎么做的），不写排期与工单（排期由 Lead 在 00 汇总里统一给）
> 与前序关系：修正并替换 02b-oss-harnesses.md 的名单基准（该文自述对比对象已过时），复用 02a 的 Claude Code／Codex 结论但补齐 2026 年新增机制

## 0. 调研方法与时效声明

### 0.1 证据分级（全文表格与要点均按此标注）

| 级别 | 含义                                                        | 本文用法                                     |
| ---- | ----------------------------------------------------------- | -------------------------------------------- |
| A    | 官方文档／规范／仓库 README 原文                            | 机制描述、能力矩阵主证据                     |
| B    | GitHub REST API 当日快照（stars／license／last push）       | 「社区规模／许可」一列，统一 2026-10-27 取数 |
| C    | 第三方横评（FlorianBruniaux/claude-code-ultimate-guide 等） | 仅用于版本号、历史定位等补充，交叉验证后使用 |
| D    | 官方口径但未独立审计（用户量、企业客户数、价格）            | 标注「官方口径」                             |
| ?    | 未找到可引用实证                                            | 表格中写「?」并在第 2 节说明，不做推断性填补 |

### 0.2 名单变更：02b 的过时项与本轮处置

| 对象                    | 2026-10-18 版结论 | 2026-10-27 事实                                                          | 本轮处置                               |
| ----------------------- | ----------------- | ------------------------------------------------------------------------ | -------------------------------------- |
| Gemini CLI              | 列为对标          | 2026-06-18 起被闭源 Antigravity CLI 取代（C）                            | 不作开源对标，仅作生态注记             |
| Aider                   | 列为停更          | 仓库最后 push 2026-05-22，最后版本 v0.86.0（B/C）                        | 保留为「停更但仍被引用」案例           |
| Continue                | 列为候选          | README 明确「仓库已只读、不再积极维护」，并发布最终 2.0.0（A）           | 降级为**形态失败案例**，不再列为对标   |
| OpenHands               | 编码 agent 平台   | 转型 Agent Canvas，并通过 ACP 接入其他 harness（v1.27.0，2026-10-12，A） | 归类到「平台／聚合器」而非编码 harness |
| MetaGPT                 | 列出              | 最后 release v0.8.1（2024-04），已休眠                                   | 移出矩阵                               |
| AutoGen                 | 列为框架          | 维护模式，主线并入 Microsoft Agent Framework（MAF）                      | 以 MAF 为现行对象                      |
| DeepSeek Harness（dsh） | 未列              | 2026-08 已 200K+ stars、developer preview；机制面与本仓库最近            | **新增参照对象**（非竞品，作同构对照） |

### 0.3 抓取限制（诚实声明，影响可复核性）

- `developers.openai.com/codex/*` 对非浏览器抓取返回 **403**；Codex 机制取自官方文档镜像 `mintlify.wiki/openai/codex/**`（同源 Mintlify 站点、页面带官方索引头）与 `openai/codex` 仓库 `docs/` 指针文件，引文一律标「镜像」。
- `agentclientprotocol.com`、`agentskills.io` 为 JS 渲染站，只确认了存在性与定位，机制细节以 OpenHands／Goose／Claude Code 文档互证，不单点引用。
- 所有 stars 为 2026-10-27 API 快照，与 02a 中 5–8 月的数字（如 Codex 86.2K、OpenCode 199K）不可直接比较，**本文不混用**。

## 1. 总览矩阵

图例：● 有一等机制 ／ ◐ 部分具备或需自建 ／ ○ 无 ／ ? 未找到实证（见第 2 节对应条目）

### 1.1 表 1｜编码类 harness 能力矩阵（6 个主力）

| 维度                          | Codex CLI                                            | Claude Code                                                 | OpenCode                             | Cline                                           | Qwen Code                              | Goose                                      |
| ----------------------------- | ---------------------------------------------------- | ----------------------------------------------------------- | ------------------------------------ | ----------------------------------------------- | -------------------------------------- | ------------------------------------------ |
| 定位                          | 终端单二进制编码 agent（Rust）                       | 闭源商业 harness + 可编程 Agent SDK                         | client/server 分离的通用编码 harness | IDE/桌面/CLI/SDK 四形态，人机协作派             | 阿里开源编码 agent，用自家模型自举迭代 | 「不止编码」的本地通用 agent，LF AAIF 治理 |
| 形态                          | CLI · exec · MCP server · app-server                 | CLI · 编辑器插件 · Agent SDK                                | TUI + 常驻 server + 桌面（BETA）     | CLI · 桌面 · VSC · JetBrains · SDK              | CLI · 桌面 · Web · IDE · IM 机器人     | 桌面 · CLI · API                           |
| agent loop／可中断            | ● 流式可打断 turn；resume／fork 会话                 | ● Esc 打断；rewind 回退到 checkpoint                        | ● 可恢复 session 为一等公民          | ● Plan→Act 两段式 + 后台续跑                    | ● ReAct + 实时打断恢复                 | ● 中断与恢复                               |
| 工具系统                      | ● 工具调用 + execpolicy 决策引擎                     | ● Pre/PostToolUse hooks 可拦截                              | ● 插件注册 + MCP                     | ● createTool + 生命周期 hooks（SDK）            | ● MCP + 内置工具                       | ● 70+ MCP 扩展 + recipe                    |
| 权限／沙箱                    | ● 3 档 sandbox + 4 档 approval + 越界 escalation     | ● OS 沙箱（仅 shell）+ 分层权限 + 分级模式                  | ◐ 逐条审批为主，无内核级沙箱（?）    | ◐ 逐条审批 + auto-approve 开关，无内核沙箱（?） | ? 未找到权限模型文档                   | ? 未找到权限模型文档                       |
| 子代理／多 agent              | ? 文档体系中未见子代理概念                           | ● 独立 context + 独立权限，按 description 委派              | ? 未见                               | ● coordinator + specialists，团队状态跨会话     | ● SubAgents + Agent Teams              | ● 异构 subagent 团队（可不同模型）         |
| 上下文工程                    | ◐ AGENTS.md 约定文件                                 | ● CLAUDE.md + Auto Memory + compact + skill 渐进加载        | ◐ AGENTS.md                          | ● .clinerules + skills 按需加载                 | ● Auto-Memory + Auto-Skills            | ◐ recipe 注入                              |
| 长期记忆                      | ◐ 文件即记忆（AGENTS.md）                            | ◐ Auto Memory（本地文件）                                   | ○                                    | ○（团队状态持久化 ≠ 长期记忆）                  | ● Auto-Memory                          | ○                                          |
| MCP client／server            | ● client，且自身可作 server（codex mcp-server）      | ● client（插件可打包 MCP）                                  | ● client                             | ● client + 市场                                 | ● client                               | ● client + 扩展生态                        |
| hooks／插件／Skills／commands | ● skills + slash commands + 受管 hooks               | ● 5 类 hook 执行器 × 3 档节奏 + plugin 打包 + 市场 + 热加载 | ● 插件                               | ● SDK plugin（工具 + hooks）+ skills            | ● skills                               | ● extensions + recipes                     |
| headless／SDK／CI             | ● exec 模式 + app-server                             | ● claude -p + Agent SDK                                     | ● headless 一等                      | ● --json headless + SDK                         | ● SDK + IM                             | ◐ CLI／API                                 |
| 可观测／trace                 | ◐ 本地 tracing／verbose 日志                         | ◐ 内置遥测导出（细节 ?）                                    | ?                                    | ◐ 日志                                          | ?                                      | ?                                          |
| 部署／自托管                  | ● 本地；企业受管配置（requirements.toml 可锁 hooks） | ◐ 本地／企业托管策略，无自托管服务端                        | ● 可自托管 server                    | ◐ 本地为主                                      | ● 本地                                 | ● 本地 + 白标发行版（CUSTOM_DISTROS）      |
| 多模型／BYOK                  | ◐ OpenAI 为主 + custom providers                     | ○ Claude 家族（经 Bedrock／Vertex 托管）                    | ● 75+ providers                      | ● 全厂商 + 聚合网关                             | ● OpenAI/Anthropic/Gemini/Qwen + 本地  | ● 15+ providers（含用订阅经 ACP）          |
| 许可／规模／商业化（B）       | Apache-2.0｜131,128★｜ChatGPT 订阅内含               | 闭源｜约 112K★（C）｜$20–200/月                             | MIT｜222,198★｜Go 订阅 $10/月        | Apache-2.0｜70,913★                             | Apache-2.0｜28,752★｜免费 + 自家模型   | Apache-2.0｜55,811★｜基金会中立治理        |

### 1.2 表 2｜平台／编排框架（5 个现行对象）

| 维度           | OpenHands Agent Canvas          | LangGraph + Deep Agents                            | CrewAI                                 | Microsoft Agent Framework                  | AgentScope 2.0                                       |
| -------------- | ------------------------------- | -------------------------------------------------- | -------------------------------------- | ------------------------------------------ | ---------------------------------------------------- |
| 定位           | 编码 agent 的**宿主/聚合器**    | 图状态机（LangGraph）+ 成套 harness（Deep Agents） | 角色扮演式多 agent 协作                | AutoGen + Semantic Kernel 合并后的企业框架 | 生产级 Python agent 框架 + agent service             |
| 编排单位       | agent 后端 × ACP provider       | 节点/边 + 子图 + subagent                          | role（goal/backstory）+ 顺序/并行/层级 | workflow 图 + actor + 跨语言运行时         | ReAct agent + TeamPipeline（leader 委派）            |
| 状态与恢复     | 后端会话；sandbox 空闲暂停      | ● checkpointer 持久化任意节点                      | ◐ 弱（官方承认复杂分支/持久化弱）      | ◐ 依赖 workflow 状态                       | ● 中断恢复 + SOP                                     |
| HITL           | ◐ automations 审批位            | ● 任意节点可暂停等人工                             | ◐ 人工步骤为节点                       | ◐                                          | ● 细粒度 confirmation + bypass mode                  |
| 工具／MCP      | ● hooks/MCP 设置项              | ● tools + MCP                                      | ● tools（不依赖 LangChain）            | ● MCP + A2A                                | ● Toolkit（Python 工具 + MCP + skills）              |
| 上下文工程     | ◐ 由 provider 决定              | ● 文件系统做上下文 + 历史摘要 + 大结果 offload     | ○ 无                                   | ◐                                          | ● 自动 compaction + tool-result offload + 注入中间件 |
| 观察面／可观测 | ◐ 会话与自动化日志              | ● LangSmith 原生                                   | ◐                                      | ◐                                          | ● 统一事件总线（reasoning/tool/多模态）              |
| 部署形态       | 桌面 + 云 + 自托管 Agent Server | 库 + 平台                                          | 库 + 云                                | 库 + Azure                                 | 库 + agent service（含 realtime voice）              |
| 许可（B）      | MIT｜91,993★                    | MIT｜43,984★ / 30,642★                             | MIT｜60,170★                           | MIT｜14,340★                               | Apache-2.0｜34,404★                                  |

### 1.3 表 3｜记忆／运行时／可观测组件（6 个）

| 维度               | Letta                                                                                                                 | mem0                                                                     | Browser Use                             | E2B / microsandbox                                                 | Langfuse                                                    | AgentOps                                 |
| ------------------ | --------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------ | --------------------------------------- | ------------------------------------------------------------------ | ----------------------------------------------------------- | ---------------------------------------- |
| 解决的问题         | 有状态、自进化 harness                                                                                                | 记忆层（抽取/检索/融合）                                                 | 浏览器动作闭环                          | 不可信代码隔离执行                                                 | trace + 评测 + prompt 管理                                  | agent 运行可观测/回放                    |
| 关键机制           | Context Constitution；MemFS（git 版本化记忆文件）；Mods 自改 harness；Computers（BYO 机器/云沙箱/teleport）；Channels | 单遍 ADD-only 抽取、entity linking、语义+BM25+实体三路检索融合、时序推理 | 31 个浏览器动作；本地/云/CDP 三种浏览器 | microVM（<100ms 启动、fork/snapshot、密钥不进 VM）；SDK 化沙箱创建 | 基于 OTel；agent graph、成本、告警、prompt 版本与对比、评测 | rewind & replay；prompt injection 审计链 |
| 接入方式           | 云/桌面/CLI/SDK；ACP+MCP                                                                                              | library / 自托管 server / 云                                             | 托管 API / CLI / Python 库              | SDK（JS/Python 等）+ MCP server + Agent Skills                     | 原生 SDK + 100+ 框架 + 经 LiteLLM 网关                      | pip install + 400+ 集成                  |
| 自托管             | ●                                                                                                                     | ●（默认开 auth）                                                         | ◐（云为商业）                           | ●（microsandbox 本地；E2B 云）                                     | ●                                                           | ?                                        |
| 许可（B）          | Apache-2.0｜25,265★                                                                                                   | Apache-2.0｜67,905★                                                      | MIT｜119,273★                           | Apache-2.0｜14,576★ / 8,822★                                       | NOASSERTION（MIT + 企业版）｜36,589★                        | 商业为主（?）                            |
| 对多租户平台的启示 | 「上下文是产品资产」的显式建模                                                                                        | 记忆写入策略不该是覆盖式更新                                             | 浏览器是可外包的运行时，不必自研        | 沙箱是可插拔组件，不该与编排耦合                                   | 观测标准接 OTel GenAI semconv                               | 回放对排障的价值高于指标                 |

### 1.4 表 4｜协议与标准基线（生态，而非产品）

| 协议／标准   | 解决什么                       | 2026-10 状态（A）                                                                                                  | 对 beauty-moon 的位置                                            |
| ------------ | ------------------------------ | ------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------- |
| MCP          | 模型↔工具／数据               | 规范 **2026-07-28**：无状态、自包含请求 + 每请求能力协商；server 侧 Tools/Resources/Prompts；Roots/Sampling 已弃用 | 已有 client + server，但形态是「有状态会话」，未对齐新规范       |
| A2A          | 不透明 agent 之间的互操作      | v1.0，已加入 Linux Foundation 的 Agentic AI Foundation；Agent Card／Task 生命周期／多租户                          | 尚无；多 agent 场景的对外接口候选                                |
| AG-UI        | agent 后端↔用户前端的事件协议 | 开源，含 Events／Interrupts／State／Subagents／Generative UI／Reasoning 事件族                                     | 前端事件（meta/delta/done）是自研，可与之对照补齐中断/子代理事件 |
| ACP          | 编辑器/宿主↔agent 进程        | OpenHands 1.27 把 Claude Code／Codex／Gemini CLI／Pi／OpenCode 作为 ACP provider 接入；Goose 用 ACP 复用订阅       | harness 可插拔的关键：loop 与 UI/编排解耦                        |
| Agent Skills | 「怎么用某能力」的可分发包装   | agentskills.io 开放标准；Claude Code、Codex、Qwen Code、mem0、Browser Use、microsandbox 均以 skill 分发接入        | 已有 Skills 上传包，但缺 SKILL.md 渐进加载与跨工具格式           |
| AGENTS.md    | 仓库约定的注入面               | agents.md 自述 60k+ 开源项目采用；Codex／OpenCode／Cline(.clinerules) 同构                                         | 无仓库约定文件的发现与注入链路                                   |

## 2. 逐产品要点（3–6 条，带来源）

### 2.1 编码类 harness

**Codex CLI**（openai/codex，Apache-2.0，131,128★）

- 沙箱是**默认开启**的执行契约：read-only／workspace-write／danger-full-access 三档，Linux 用 Landlock+seccomp（+实验性 Bubblewrap），macOS 用 Seatbelt，Windows 用受限令牌 + job object；.git/ 与 .codex/ 始终只读且**递归生效**（[镜像·sandboxing](https://mintlify.wiki/openai/codex/concepts/sandboxing.md)）。
- 网络与文件系统**分开控制**（sandbox_policy.network_access），这比「沙箱开/关」二值开关更贴近真实风险模型（同上）。
- 审批有 4 档策略：on-request（默认，只在越界时问）、unless-trusted、on-failure（先跑沙箱失败再问）、never；越界时给 4 个决策：一次／本会话／写入策略／拒绝（+ cancel turn）（[镜像·approvals](https://mintlify.wiki/openai/codex/concepts/approvals.md)）。
- 策略引擎用 **Starlark 前缀规则**（prefix_rule：allow/prompt/forbidden）+ host_executable 限定可执行文件绝对路径，规则落 .codex/execpolicy/（同上）。这是「命令级」而非「工具级」的权限粒度。
- 会话模型是 **Threads / Turns / Items 三层**：turn 有 inProgress/completed/interrupted/failed 状态且可 turn/interrupt 中断；item 分 agentMessage／reasoning／plan／commandExecution／fileChange／mcpToolCall／webSearch 等类型；thread 以 JSONL rollout 落盘，可 resume／fork（[镜像·overview](https://mintlify.wiki/openai/codex/concepts/overview.md)）。
- 对外原语：codex mcp-server（自身当 MCP server）、codex app-server（给 IDE 用）、codex exec（CI）（[镜像索引](https://mintlify.wiki/openai/codex/llms.txt)）。
- 企业面有受管层：requirements.toml 可设 allow_managed_hooks_only 锁死 hooks（[仓库 docs/config.md](https://github.com/openai/codex/blob/main/docs/config.md)）。

**Claude Code**（闭源 + Agent SDK，约 112K★）

- 沙箱与审批是**两条腿**：OS 强制沙箱（macOS/Linux/WSL2，原生 Windows 不沙箱）只覆盖 shell，因此「沙箱内命令不必逐条审批」；文件工具／MCP／hooks 在沙箱外跑，仍走权限系统（[sandboxing](https://code.claude.com/docs/en/sandboxing.md)）。
- 权限是分层的：只读工具在工作目录内免批、Bash 除内置只读命令外需批、WebFetch 有预批准域白名单；「don't ask again」写 .claude/settings.local.json（[permissions](https://code.claude.com/docs/en/permissions.md)）。
- hooks 是**生命周期契约**：5 类执行器（shell／HTTP／MCP tool／LLM prompt／subagent）× 3 档节奏（每 session／每 turn／每次工具调用），PreToolUse 可拦截——即产品级「插件点在 loop 上」（[hooks](https://code.claude.com/docs/en/hooks.md)）。
- Skills 关键在**延迟加载**：SKILL.md 正文只在被调用时进 context；且 .claude/commands/x.md 与 .claude/skills/x/SKILL.md 等价，命令即 skill（[skills](https://code.claude.com/docs/en/skills.md)）。
- checkpoint 是**turn 级快照**：每个发起 turn 的 prompt 存文件快照，保留最近 100 个，/rewind 可选恢复代码或对话，约 30 天后清理（[checkpointing](https://code.claude.com/docs/en/checkpointing.md)）。
- subagent 的差异化在**隔离**：独立 context window + 自定义 system prompt + 特定工具集 + 独立权限，按 description 自动委派；配合 background agents／cross-session messaging（[sub-agents](https://code.claude.com/docs/en/sub-agents.md)）。
- plugin 把 Skills/Agents/Hooks/MCP 打成一个可分发单元，有市场，且 TS SDK 支持会话中热加载（[plugins](https://code.claude.com/docs/en/agent-sdk/plugins.md)）。计费方面 2026-06-15 宣布的 programmatic 计费后被暂停，2026-09-30 仍走订阅（C）。

**OpenCode**（anomalyco/opencode，MIT，222,198★）

- 架构洞察是 **client/server 分离**：agent 跑在常驻 server 进程，terminal／IDE／远程机器都只是 client；因此 headless 与「可恢复 session」是架构副产品而非附加功能（A/B）。
- 75+ providers、插件与 MCP 扩展、AGENTS.md 约定；商业化是 opencode Go 订阅 $10/月（单席位、$12/5h 限额、超限回退余额）（B/C）。
- 2026-10 仍在高频迭代（当日 push），是「社区规模 + 服务端架构」组合的胜出样本。

**Cline**（cline/cline，Apache-2.0，70,913★）

- 人机协作派的最完整形态：Plan 模式先探索/澄清/出策略，Act 模式执行，每个文件编辑与终端命令默认需批准，可切 auto-approve（[README](https://github.com/cline/cline)）。
- 规则与技能分离：.clinerules 常驻约定，skills 让模型按需加载具体规则（同上）。
- 多 agent 团队：coordinator 拆子任务委派给 specialists，各自有工具与上下文，**团队状态跨会话持久化**（cline --team-name）；另有 cron 定时 agent、IM（Telegram/Slack/Discord/WhatsApp/Linear）接入并带访问控制（同上）。
- headless 有 --json 事件流可直接进 CI；SDK 侧 createTool + 生命周期 hooks（logging/审计/策略）是可编程插件点（同上）。

**Qwen Code**（QwenLM/qwen-code，Apache-2.0，28,752★）

- 机制清单直接对齐第一梯队：Auto-Memory、Auto-Skills、SubAgents、Agent Teams、MCP、动态 workflow，开箱即用（[README](https://github.com/QwenLM/qwen-code)）。
- 多协议 + 运行时切换（OpenAI/Anthropic/Gemini/Qwen，本地 Ollama/vLLM），分发面覆盖 terminal、桌面、Web、IDE 插件、IM 机器人（Telegram/DingTalk/WeChat/Feishu）（同上）。
- 自我迭代闭环（用自家 agent 提 issue/PR/评审）是「用自己吃自己的狗粮」的规模化验证，值得作为验收手段参考（同上，官方口径）。

**Goose**（aaif-goose/goose，Apache-2.0，55,811★）

- 不走「模型厂绑定」路线：15+ providers，且能通过 ACP 直接借用 Claude/ChatGPT/Gemini 的**订阅额度**（[README 生态描述/C]）。
- 扩展面是 70+ MCP extensions + recipe（可复用工作流），并且有**异构 subagent 团队**（每个 subagent 可用不同模型）——这是低成本异构编排的实用解（同上）。
- CUSTOM_DISTROS.md 支持自有发行版/白标，加上移交 Linux Foundation AAIF 治理，是「中立可嵌入」的典型商业化路径（B/C）。

**SWE-agent**（SWE-agent/SWE-agent，MIT，20,651★）

- 学术侧最有价值的概念是 **ACI（Agent-Computer Interface）**：把终端/编辑/测试抽象成一套受约束的命令，而不是给模型一个裸 shell（C，NeurIPS 2024 论文线）。
- 可复现性来自 trajectory 日志 + eval harness + 配置 YAML；成本可控（论文 SWE-bench 全量平均 $1.59/实例，$4 上限）（C）。
- 对平台的意义：**评测集与轨迹格式**是可复用的基础设施，比 agent 框架本身更长寿。

**Aider**（Aider-AI/aider，Apache-2.0，49,770★，停更）

- 停更的判定要精确：最后版本 v0.86.0（2025-08），仓库最后 push 2026-05-22（B/C）；star 仍在涨，说明需求在但供给撤了。
- 留下的两个机制仍有参考价值：tree-sitter 符号图做 repo map（把仓库地图当上下文预算问题解）、每次修改自动 commit（变更即可回滚的最小单位）（C）。

**Continue**（continuedev/continue，Apache-2.0，36,345★）

- README 首段明写「仓库已不再积极维护、对所有用户只读」，并做了最终 2.0.0 发布（移除了匿名遥测与登录）（A）。
- 教训比机制更值钱：**IDE 插件形态的单点 OSS 编码 agent** 在 2026 被 CLI + 桌面 + SDK 三形态吞掉；只做「编辑器里的聊天 + 补全」不再构成护城河。

### 2.2 平台／编排框架

**OpenHands Agent Canvas**（OpenHands/OpenHands，MIT，91,993★）

- 战略转向最值得注意：1.27.0（2026-10-12）把 Claude Code、Codex、Gemini CLI、Pi、OpenCode 作为**内置 ACP provider**，Canvas 自己不持有 loop（[release notes](https://docs.openhands.dev/openhands/usage/agent-canvas/release-notes/v1.27.0.md)）。
- 后端是可插拔的：本地机器、自托管 Agent Server、DigitalOcean Managed Agents（空闲自动暂停的隔离沙箱）（同上）。
- 治理面在收敛为**automations**：带 setup assistant 的表单式自动化，可持久化草稿、测试、试跑；use case 里甚至给出「四条 automation 串起 issue→开发→评审→测试→合并」的 software factory（同上）。
- 开源核心仍是执行图 + 沙箱；预算/护栏/插件市场/审计等治理层在付费版（C）。这与 beauty-moon「自托管多租户」的位置正好互补而非冲突。

**LangGraph + Deep Agents**（MIT，43,984★ / 30,642★）

- LangGraph 的价值是把 agent 当**状态机**：typed state + 条件边 + checkpointer（任意节点持久化）+ HITL 一等公民（可在节点暂停等人工）（C/A）。
- Deep Agents 把「上下文工程」做成默认六件套：在环境里行动、按需加载（memories/skills）、管理膨胀上下文（摘要 + 大结果 offload）、并行子代理（隔离 context window）、HITL 中断、按使用更新记忆/技能/prompt（[docs](https://docs.langchain.com/oss/python/deepagents/overview.md)）。
- 洞察：它把「文件系统」当作上下文管理的主语——这比 prompt 拼接更可压缩、可回溯、可交接。

**CrewAI**（MIT，60,170★）

- role/goal/backstory + sequential/parallel/hierarchical 是最容易被业务方理解的编排 DSL，2B+ 任务执行的官方口径说明其真实用量（C/D）。
- 官方也承认弱项是复杂条件分支与持久化执行——即「DSL 抽象换来的可读性，代价是控制流表达力」（C）。
- 可借鉴其**角色即权限边界**的隐式约定：role 天然对应工具集与预算。

**Microsoft Agent Framework**（MIT，14,340★）

- AutoGen（61,585★）已进维护模式，主线并入 MAF：AutoGen 的 actor 模型 + Semantic Kernel 的企业能力，Python/C#/TS 跨运行时（C/A）。
- 双协议一手抓：实现 A2A、同时支持 MCP，是「企业内既有资产接入」的现实解（C）。
- 对平台方的含义：跨语言与协议兼容是采购门槛，而不是技术炫技。

**AgentScope 2.0**（Apache-2.0，34,404★）

- 结构最接近「一个平台该有的分层」：ReAct、Toolkit（Python 工具 + MCP + skills + 内置 shell/file/search + task/plan 工具）、Model（多 provider + 按回复选模型的 ModelRouterMiddleware）、Context（自动 compaction + tool-result offload + 注入中间件）、统一事件总线、Permission & HITL（细粒度 + confirmation + bypass）（[README](https://github.com/agentscope-ai/agentscope)）。
- **Middleware 是可组合横切 hooks**（reply/reasoning/acting/model calling/permission/压缩/system prompt）——把 Claude Code 的 hooks 概念升级成框架原语（同上）。
- 沙箱后端有 8 种（local/Docker/Apple Container/Bubblewrap/E2B/OpenSandbox/Daytona/K8s），即「执行环境是策略」而非架构（同上）。
- 2026-09 起补齐 TeamPipeline（leader 委派）、SOP、A2A（A2AAgent）；2026-10 加 realtime voice（WebRTC，语音与文本共享会话历史）（同上，时间线明确）。

### 2.3 低代码／应用平台

**Dify**（Apache-2.0 主体 + 附加条件，159,696★）

- 能力截面是七件套：可视化 Workflow、数百模型、Prompt IDE、RAG pipeline、Agent（自带 sandbox：跑命令/装软件/管文件，可接 Marketplace 工具、MCP server、自有 API）、LLMOps、BaaS（[README](https://github.com/langgenius/dify)）。
- 关键设计：所有能力都有 API（BaaS），因此「低代码」不是封闭 IDE，而是可以被平台二次编排的能力层。
- 观测不自研，直接原生集成 Opik／Langfuse／Arize Phoenix——把可观测当插槽（同上）。

**n8n**（fair-code / Sustainable Use，208,746★）

- 定位已从「工作流自动化」改为「AI agent + workflow 平台」：1500+ 集成、9000+ 模板、可视化画布 + 可写 JS/Python/npm（[README](https://github.com/n8n-io/n8n)）。
- 企业面写得很实：RBAC、审计轨迹、可自托管、human approvals 在流程中作为节点（同上）。
- 许可形态值得记：源码可见、可自托管、可扩展，但商用按 Sustainable Use License + 企业版授权——这是「开源可嵌入 + 商业授权」的成熟模板。

**Coze Studio**（Apache-2.0，21,766★）

- 国内大厂把核心引擎开源的样本：Go 后端 + React/TS 前端，微服务 + DDD，能力覆盖模型服务、agent/app 构建、workflow、plugins、知识库、database（[README](https://github.com/coze-dev/coze-studio)）。
- 最后 push 2026-07-29（B），节奏明显放缓——说明「开源核心引擎」策略若不持续投入，很难维持社区势能。

### 2.4 记忆／运行时／可观测

**Letta**（Apache-2.0，25,265★）

- 最强的一条洞察是 **Context Constitution**：把「上下文里放什么、什么顺序、多细、放多久」写成显式规则，agent 主动管理自己的 context（token 空间即身份与连续性），不靠改权重（[docs 索引](https://docs.letta.com/llms.txt)）。
- MemFS 用 git 版本化记忆文件——记忆的**可审计性**来自版本控制，而不是 embedding 相似度（同上）。
- Computers 三态（带自己的机器／云沙箱／teleport）说明「执行环境可迁移」已被当成产品能力；Channels 覆盖 Slack/Telegram/Discord/WhatsApp/Signal + 自动回复中继（同上）。
- Mods 允许 agent 自改 harness（加工具、加命令、改上下文注入、加 provider）——把 harness 本身变成可被 agent 编辑的对象（同上）。

**mem0**（Apache-2.0，67,905★）

- 2026-04 的算法转向值得单独记：从「用 LLM 判断 UPDATE/DELETE」改为**单遍 ADD-only 抽取**，记忆只累加不覆盖，靠 entity linking + 语义/BM25/实体三路检索融合 + 时序推理来消歧（[README](https://github.com/mem0ai/mem0)）。
- 效果数字（官方口径）：LoCoMo 71.4→92.5、LongMemEval 67.8→94.4、BEAM(1M) 64.1，检索 token 约 6.7–7.0K、p50 0.88–1.09s（同上）。
- 洞察：写入端少做判断、检索端多做融合，比「智能合并」更稳、更便宜、更可审计——因为抽取不可逆、检索可重排。
- 分发方式本身也是信号：以 Agent Skills 形式（npx skills add）教编码 agent 接入，覆盖 Claude Code/Codex/Cursor/OpenCode/DeepSeek Harness 等（同上）。

**Browser Use**（MIT，119,273★）

- 把「浏览器」做成可外包运行时：开源 Python/TS 库可驱动本地浏览器，也可驱动 $0.02/浏览器小时的云浏览器（stealth、CAPTCHA、住宅代理、live view URL）（[README](https://github.com/browser-use/browser-use)）。
- 兼容性策略值得学：实现 Claude 的 31 个 browser actions，并能接本地/云/CDP 远程浏览器——对齐别人的工具契约，而不是另造一套（同上）。
- 三种交付路径（托管 agent API／CLI 一键注册 skill／Python 库）说明同一能力要按「运维能力」分层售卖。

**E2B / microsandbox**（Apache-2.0，14,576★ / 8,822★）

- E2B 提供的是「云端隔离执行 + 代码解释器」SDK 化能力（Sandbox.create → commands.run／runCode）（[README](https://github.com/e2b-dev/E2B)）。
- microsandbox 走本地 microVM 路线：硬件级隔离、<100ms 启动、**fork 活沙箱并快照恢复**、OCI 镜像兼容、密钥永不进入 VM，并自带 MCP server 与 Agent Skills（[README](https://github.com/superradcompany/microsandbox)）。
- 洞察：沙箱的关键指标已从「有没有隔离」变成「**快照/分叉成本**」——因为它决定 agent 能否试错式地并行探索。

**Langfuse**（MIT + 企业版，36,589★）

- 观测口径基于 OpenTelemetry：trace 覆盖 LLM 与非 LLM 调用（检索/嵌入/API）、session 聚合多轮、按 user 归因成本、agent graph、告警（Slack/GitHub Actions/Webhook）（[docs](https://langfuse.com/docs)）。
- 产品侧把 prompt 当**受版本控制的部署物**（版本/部署/对比）并与评测联动——这是「prompt 即代码」的落地形态（同上）。
- 对平台的含义：观测不应自成一派，而要能被 Litellm/OTel 这类中间层透传（同上，支持经 LiteLLM 网关接入）。

**AgentOps**（商业为主）

- 差异化是 **time-travel debugging**：rewind & replay 单次 agent run 到任意时间点，配合日志/错误/prompt injection 审计链（[官网](https://www.agentops.ai/)）。
- 洞察：对 agent 排障来说，「能重放到出问题那一步」比「有指标面板」价值高一个量级。
- 生态位靠集成数量铺开（400+ LLM/框架），这本身说明观测层是「接入密度」生意。

**OpenTelemetry GenAI semconv**（生态基线，semconv 1.44.0）

- 现行分组含 Gen AI、OpenAI、MCP；即「MCP 调用」已被纳入标准属性族（[semconv](https://opentelemetry.io/docs/specs/semconv/gen-ai/)）。
- 它是唯一「不选边厂商」的语义约定，跨厂商复用 dashboard/告警/评测的前提；自研字段可以有一层封装，但 span 语义最好直连 semconv。

## 3. 2026 年共性趋势（以及为什么成为趋势）

1. **审批范式从「逐条问」转向「默认沙箱内自动跑，越界才审批」**。Codex 的 3 档 sandbox × 4 档 approval + escalation 三级记忆（一次/会话/策略）、Claude Code 用 OS 沙箱换取「沙箱内免批」都是同一结论：审批疲劳会让用户一键全放开，安全性反而下降；把安全边界交给内核/OS，把人工判断留给越界动作。
2. **权限粒度从「工具」下移到「路径/命令前缀」**。Codex 的 protected paths（.git/ 递归只读）+ Starlark prefix_rule + host_executable、Claude Code 的 filesystem.denyRead 都说明：MCP/插件让工具集不可枚举，只能对**行为**建规则。
3. **Skill 取代 SDK 成为「接入指南」的分发单元**。agentskills.io 被 Claude Code/Codex/Qwen Code 支持，mem0/Browser Use/microsandbox 直接以 skill 交付接入方式；AGENTS.md 已有 60k+ 项目采用。原因是 MCP 回答「能给什么能力」，skill 回答「怎么用」，且 skill 是 prompt+脚本的可版本化包装，**分发时不需要改宿主代码**。
4. **恢复粒度三层化**：turn 级（Claude Code 100 个 checkpoint、LangGraph checkpointer）、机器级（Letta Computers/teleport、microsandbox fork/snapshot）、会话级（Codex resume/fork、OpenCode 可恢复 session）。原因：长任务失败是常态，**恢复成本决定可用性**，快照远比重跑便宜。
5. **harness 层解耦、宿主层聚合**。OpenHands Canvas 1.27 用 ACP 接入 Claude Code/Codex/Gemini CLI/Pi/OpenCode；Goose 用 ACP 借用订阅额度；Cline 的 JetBrains 插件只是「共享 agent core 的客户端」。模型与 loop 迭代太快，没人愿意把 UI/编排/沙箱绑死在一个 loop 上。
6. **协议四层定型且互不替代**：MCP（工具/数据，2026-07-28 转无状态 + 每请求协商）、A2A（agent 之间，v1.0 入 LF）、AG-UI（后端↔前端事件，含 Interrupts/Subagents/State）、ACP（client↔agent 进程）。原因：它们各自对应不同信任边界，混用一个协议会让能力模型与权限模型互相污染。
7. **记忆算法转向「累加式 + 检索侧消歧」**。mem0 用单遍 ADD-only 抽取替代 LLM 的 UPDATE/DELETE 判断，配 entity linking 与三路检索融合把 LoCoMo 从 71.4 拉到 92.5。原因：让 LLM 决定「该遗忘/该覆盖」不可靠且贵，而 append + 检索重排可逆、可审计。
8. **商业化形状高度一致：开源核心执行层 + 付费治理层**。OpenHands 的预算/护栏/市场、n8n 的 Sustainable Use + 企业版、Dify 的附加条件、mem0 托管版含非开源优化、OpenCode 的 $10 订阅——执行层可复制，企业只为配额/审计/成本/合规付费。
9. **生态洗牌加速，纯 IDE 插件形态被吞并**。Continue 仓库只读、Aider 停更（最后 push 2026-05-22）、MetaGPT 休眠、OpenHands 转型聚合。赢家要么绑定模型/订阅（Codex/Claude Code/Qwen Code），要么有 20 万级社区 + 服务端架构（OpenCode）。

## 4. 对 beauty-moon 的机制启示（只写机制层）

结合 00-summary 的现状（run 状态机/事件溯源/工具注册表/MCP client+server/Skills 上传包/只读代码工具/jobs/沙箱 P1–P3/usage 生产者/OTel+Sentry），差距不在「有没有某能力」，而在「能力的形状」：

1. **审批需要「决策引擎 + 三级记忆」，而不是 run 级二元确认**。现状是审批挂在 run 上；矩阵里三家（Codex/Claude Code/AgentScope）都收敛到「命令/路径级规则 + 一次/会话/永久三级记忆 + 越界 escalation 通道」。机制点：工具执行前插入 policy 评估（allow/prompt/forbidden），决策结果与规则来源进事件流，才可审计。
2. **沙箱边界要与审批边界对齐**。Codex 的做法（OS 沙箱内自动、越界才问）说明「沙箱」和「审批」必须共享同一套边界定义；beauty-moon 沙箱在 workspace-service、审批在 orchestrator，两边各写一份边界就会漂移。机制点：边界（可写路径/网络/受保护路径）单点定义，沙箱执行器与 policy 引擎共同消费。
3. **checkpoint 的产品面缺的不是存储，而是「恢复语义」**。矩阵里 turn 级快照（Claude Code）、图状态（LangGraph）、机器快照（microsandbox）三层机理不同；beauty-moon 已有事件溯源（可作为逻辑恢复），但缺文件快照与「回退后 run 状态机怎么继续」的定义。机制点：先定义回退的语义（恢复到哪个 turn、产物如何对齐、审批记录是否重放），再决定快照载体。
4. **上下文工程应做成 orchestrator 的可插拔管线**，而不是写死在 prompt 组装里。Deep Agents（摘要 + 大结果 offload + 文件系统）、AgentScope（compaction/offload/注入中间件）、Letta（Context Constitution）都把它做成策略层。机制点：压缩阈值、offload 目标、注入顺序（AGENTS.md/技能/记忆）作为配置，随 run 一起落事件，便于复现。
5. **子代理的最小可行形态是「隔离 context + 只回摘要」**，不是完整多 agent 编排。Claude Code（独立 context/权限）、Deep Agents（隔离窗口并行）、Cline（团队状态跨会话）共同点：父级只接收摘要与产物引用。机制点：在现有 run 图里加「委派节点」，子 run 复用同一事件/工具/沙箱基建，避免另起一套编排。
6. **MCP 形态需要对齐 2026-07-28 规范**：无状态自包含请求 + 每请求能力协商；当前的「有状态会话」假设在跨租户/多副本部署下会成为约束。机制点：把 MCP 会话状态外置为 run/连接级存储，而不是进程内。
7. **前端事件流值得按 AG-UI 的事件族补三类**：Interrupts（可恢复的中断）、Subagents（子代理可见性）、State（状态快照/差分）。beauty-moon 现在是 meta/delta/done 三件套，能表达流式文本但表达不了「中断后可续」与「子代理进度」。
8. **事件模型值得对齐「thread / turn / item」三级 + plan 作为一等 item**。Codex 把 plan、commandExecution、fileChange、mcpToolCall、webSearch 都定义成带 started/delta/completed 生命周期的事件类型，天然支撑「进行中可视化 + 中断 + 回放」；beauty-moon 已有事件溯源，缺的是**事件 kind 的分类学**（以及 plan/Todo 作为结构化事件而非消息）。
9. **观测与成本要按 run/turn 归因，并对齐 OTel GenAI semconv**（含 MCP 分组）。已有 usage 生产者是优势，缺的是把 token/成本/工具调用挂到同一 trace 树上，才能支撑配额、告警与评测。

### 4.1 我认为 beauty-moon 最该补的 5 个机制

1. **工具执行 policy 引擎**（命令/路径级 allow·prompt·forbidden + 一次/会话/永久三级审批 + 越界 escalation），并把决策写入事件流。
2. **run 取消/超时 + 会话级 checkpoint 与回退语义**（先定恢复语义，再定快照载体；含回退后状态机如何继续）。
3. **上下文工程管线**（压缩 + 大结果 offload + SKILL.md 渐进加载 + AGENTS.md 约定文件注入），做成 orchestrator 的可插拔策略。
4. **隔离式子代理（委派节点）**：独立 context window、受限工具集、父级只收摘要与产物引用。
5. **可观测/成本闭环**：OTel GenAI semconv 对齐 + 按 run/turn 的 token/成本归因 + 从 trace 到评测（可回放）的链路。

## 5. 来源清单

### 5.1 一手文档／仓库（A）

- Codex 文档镜像索引：https://mintlify.wiki/openai/codex/llms.txt ｜ 核心概念（Thread/Turn/Item）：https://mintlify.wiki/openai/codex/concepts/overview.md ｜ 沙箱：https://mintlify.wiki/openai/codex/concepts/sandboxing.md ｜ 审批与 execpolicy：https://mintlify.wiki/openai/codex/concepts/approvals.md
- Codex 仓库（含 docs 指针与受管 hooks 说明）：https://github.com/openai/codex ｜ https://github.com/openai/codex/blob/main/docs/config.md
- Claude Code｜权限：https://code.claude.com/docs/en/permissions.md ｜ 沙箱：https://code.claude.com/docs/en/sandboxing.md ｜ hooks：https://code.claude.com/docs/en/hooks.md ｜ skills：https://code.claude.com/docs/en/skills.md ｜ subagents：https://code.claude.com/docs/en/sub-agents.md ｜ checkpoint：https://code.claude.com/docs/en/checkpointing.md ｜ plugins：https://code.claude.com/docs/en/agent-sdk/plugins.md
- OpenCode：https://github.com/anomalyco/opencode ｜ Cline：https://github.com/cline/cline ｜ Qwen Code：https://github.com/QwenLM/qwen-code ｜ Goose：https://github.com/aaif-goose/goose
- SWE-agent：https://github.com/SWE-agent/SWE-agent ｜ Aider：https://github.com/Aider-AI/aider ｜ Continue：https://github.com/continuedev/continue
- OpenHands Agent Canvas 1.27.0 发布说明：https://docs.openhands.dev/openhands/usage/agent-canvas/release-notes/v1.27.0.md ｜ 仓库：https://github.com/OpenHands/OpenHands
- Deep Agents：https://docs.langchain.com/oss/python/deepagents/overview.md ｜ 仓库：https://github.com/langchain-ai/deepagents ｜ LangGraph：https://github.com/langchain-ai/langgraph
- CrewAI：https://github.com/crewAIInc/crewAI ｜ MAF：https://github.com/microsoft/agent-framework ｜ AgentScope：https://github.com/agentscope-ai/agentscope
- Dify：https://github.com/langgenius/dify ｜ n8n：https://github.com/n8n-io/n8n ｜ Coze Studio：https://github.com/coze-dev/coze-studio
- Letta：https://docs.letta.com/llms.txt ｜ mem0：https://github.com/mem0ai/mem0 ｜ Browser Use：https://github.com/browser-use/browser-use
- E2B：https://github.com/e2b-dev/E2B ｜ microsandbox：https://github.com/superradcompany/microsandbox
- Langfuse：https://langfuse.com/docs ｜ AgentOps：https://www.agentops.ai/
- 规范：MCP https://modelcontextprotocol.io/specification/latest ｜ A2A https://a2a-protocol.org/latest/ ｜ AG-UI https://docs.ag-ui.com/ ｜ ACP https://agentclientprotocol.com/ ｜ Agent Skills https://agentskills.io/ ｜ AGENTS.md https://agents.md/
- OTel GenAI semconv：https://opentelemetry.io/docs/specs/semconv/gen-ai/

### 5.2 快照与二手（B/C）

- GitHub API 当日快照（stars/license/last push）：https://api.github.com/repos/openai/codex 等，逐一对应本文表格；取数日 2026-10-27。
- 第三方横评（版本/历史定位）：https://raw.githubusercontent.com/FlorianBruniaux/claude-code-ultimate-guide/main/guide/ecosystem/agentic-tools.md

### 5.3 待核实（不进入表格结论）

- microsandbox 的生产可用性与性能数据来自其 README 自述，未做独立压测。
- microsoft/autogen 的 license 元数据显示 CC-BY-4.0（疑为 LICENSE 文件覆盖范围问题），实际代码许可待核实。
- continuedev/continue 的 pushed_at 仍显示当日更新，与 README「只读/停止维护」自述冲突，疑为自动化提交，待核实。
- Langfuse 与 Dify、n8n 的 OSI 许可均为附加条件型（NOASSERTION），商用条款需法务确认后再引用。
- OpenCode/Cline/Qwen Code/Goose 的「无内核级沙箱」判断基于文档未见，而非文档明确否定——标为 ?/◐ 而非 ○。
- Claude Code 的 stars 与 v0.x SDK 版本号来自二手指南，未用官方口径核实。
