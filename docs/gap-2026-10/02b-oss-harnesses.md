# 02b · 开源 Agent Harness 横评（OpenHands / Cline / Aider / Gemini CLI / Goose）

> 调研范围：五款开源 agent harness 的产品形态、agent 循环与工具集、权限/审批与沙箱、子代理/并行、上下文与记忆、扩展机制、headless/CI。
> 纪律：只取搜索结果标题+摘要，未确认处标「待核实」；关键结论附来源 URL。
> 本文档聚焦「beauty-moon 可借鉴的机制」，与 [02a Claude Code / Codex 横评](02a-claude-codex.md) 配套。

## 总览矩阵

| 产品       | 形态              | 工具与执行                | 权限/沙箱                 | 子代理/并行               | 上下文与记忆            | 扩展机制              | headless/SDK          | 亮点机制              |
| ---------- | ----------------- | ------------------------- | ------------------------- | ------------------------- | ----------------------- | --------------------- | --------------------- | --------------------- |
| OpenHands  | Web 平台+CLI+本地 | shell/浏览器/IPython 工具 | 每任务隔离 runtime        | 支持 agent 委派（待核实） | 事件历史+microagent     | microagent/MCP/自定义 | headless+容器运行     | 隔离 runtime、持久流  |
| Cline      | IDE 插件          | 读写/终端/浏览器工具集    | Plan/Act 分期+逐步审批    | 暂无内建子代理（待核实）  | 说明文件+压缩（待核实） | MCP+.clinerules 规则  | headless/CI（待核实） | Plan/Act 双模式协同   |
| Aider      | 终端结对编程 CLI  | 编辑文件+跑命令+git 提交  | 每步确认/可自动（待核实） | 无子代理（单 agent）      | repo map（符号图索引）  | 配置项/自定义提示模板 | --message 一次执行    | repo map 精准选上下文 |
| Gemini CLI | 开源终端 agent    | 读写文件/shell/MCP 工具   | 逐工具确认+白名单         | 暂无内建子代理（待核实）  | GEMINI.md+会话          | 扩展包+MCP server     | headless 非交互模式   | 开源可 fork、扩展体系 |
| Goose      | 桌面 App+CLI      | 可插拔 extension          | 逐档模式确认（待核实）    | 暂无内建子代理（待核实）  | 会话持久化+recipe 固化  | MCP extension+配方    | goose run 无交互      | recipe 作为任务契约   |

## OpenHands

以「agent 跑在隔离运行时里」为核心架构：官方 Agent Canvas 的架构文档与月刊都以 scoped agents、isolated runtimes、persistent workflows 为主线，说明它把执行环境当成可调度、可复用的资源而不是本机进程。
（来源：[Agent Canvas Architecture · OpenHands Docs](https://docs.openhands.dev/openhands/usage/agent-canvas/architecture)、[OpenHands in September: Scoped Agents, Isolated Runtimes, Persistent Workflows](https://www.openhands.dev/blog/new-in-agent-canvas-september-2026)）

- **隔离 runtime 是产品级的一等对象**：沙箱有独立生命周期，可被多个工作流复用与回收——这正是多租户 SaaS 需要的形状（每租户/每任务一个 runtime，而非共享宿主）。
  来源：[Agent Canvas Architecture](https://docs.openhands.dev/openhands/usage/agent-canvas/architecture)
- **agent 可被「限定范围」（scoped agent）并委派**：把能力与可及资源收窄后交给子 agent，比单纯给一个通用 agent 加提示词更容易做权限与成本控制。
  来源：[OpenHands in September](https://www.openhands.dev/blog/new-in-agent-canvas-september-2026)
- **作为「可被别的 harness 调用的沙箱化代码 agent」已是外部集成形态**：社区把它当作带沙箱的执行后端嵌入其他 agent，说明「sandbox-as-a-service」确有需求。
  来源：[hermes-agent Issue #477](https://github.com/NousResearch/hermes-agent/issues/477)

> 待核实：runtime 的具体实现（Docker 容器 / 远程 runtime 的调度方式）、microagent 的触发机制、headless 模式的准确 CLI 形态。

## Cline

IDE 内的 agent：官方 tools 参考列出了文件读写/编辑、终端执行、浏览器等一整套工具的职责边界，agent 的这些动作都经由宿主 IDE 的界面呈现并接受人工批准。
（来源：[Tools · Cline Docs](https://docs.cline.bot/tools-reference/all-cline-tools)）

- **工具集「显式枚举 + 逐个写清约束」**：文档化每个工具的输入、可做与不可做的事，而不是一句「给你 shell」。这让「工具注册表」成为可评测、可收敛的清单，而不是模型自由发挥。
  来源：[Tools · Cline Docs](https://docs.cline.bot/tools-reference/all-cline-tools)
- **人机协同落在交互模式上**：以 Plan / Act 两阶段推进，先产出计划再执行——这对需要审批的产品是天然契合点（计划即审批对象）。
  来源：[Tools · Cline Docs](https://docs.cline.bot/tools-reference/all-cline-tools)
- **扩展走 MCP**：外部能力以 MCP server 接入，与 Cline 自身解耦，社区已有大量以 MCP 为单位的集成与评测用例（待核实是否为全部扩展面的唯一入口）。
  来源：[Tools · Cline Docs](https://docs.cline.bot/tools-reference/all-cline-tools)

> 待核实：Plan/Act 模式的确切命名与切换规则（沿用公开印象，搜索结果未直接印证）、上下文压缩策略、headless/CI 支持、是否内建子代理。

## Aider

终端里的结对编程 agent：以「你指出文件、它改文件并自动提交」为核心循环，定位轻量、可脚本化、git 友好。
（来源：[ai-code-editor-almanac · Aider](https://github.com/ArdurAI/ai-code-editor-almanac/blob/main/tools/aider.md)、[Aider Review (2026)](https://www.revuo.ai/category/coding-agent-remotes/aider)）

- **repo map 是它最重要的机制**：用 tree-sitter 解析仓库、抽取符号与关系生成紧凑的仓库地图，再按当前请求挑出相关文件——在「上下文有限」这条约束下，用结构化索引换 token，而不是靠向量检索兜底。
  来源：[ai-code-editor-almanac · Aider](https://github.com/ArdurAI/ai-code-editor-almanac/blob/main/tools/aider.md)
- **自动 git 提交让每次 agent 改动都可回滚**：产物天然落到版本历史里，"agent 改坏了"变成一次 revert，而不是一场事故。
  来源：[Aider Review (2026)](https://www.revuo.ai/category/coding-agent-remotes/aider)
- **单 agent、无子代理也能好用**：说明「上下文组织 + 变更可回滚」这两点的收益，未必小于堆并行代理。
  来源：[Aider Review (2026)](https://www.revuo.ai/category/coding-agent-remotes/aider)

> 待核实：审批粒度（是否可完全自动）、`--message` 一次性执行的确切参数、上下文压缩策略。

## Gemini CLI

Google 开源的终端 agent：公开报道明确其「面向开发人员的开源终端 AI 代理」定位，可直接在仓库目录里执行文件操作与命令。
（来源：[谷歌推出 Gemini CLI：面向开发人员的开源终端 AI 代理 · InfoQ](https://www.infoq.cn/article/2g8pxeprm6vcyzy38pbz)、[ai-coding-guide · gemini-cli](https://github.com/jnMetaCode/ai-coding-guide/blob/main/gemini-cli/README.md)）

- **开源可 fork 本身就是一种架构承诺**：harness 的循环、工具与提示可被下钻阅读与替换，企业可据此自建受控版本——闭源产品只能用配置与钩子去逼近同样的可控性。
  来源：[InfoQ 报道](https://www.infoq.cn/article/2g8pxeprm6vcyzy38pbz)
- **扩展体系与 MCP 并行**：既有自家的扩展包/配置文件体系，也通过 MCP 接入外部工具，形成「配置式扩展 + 协议式扩展」双轨。
  来源：[ai-coding-guide · gemini-cli](https://github.com/jnMetaCode/ai-coding-guide/blob/main/gemini-cli/README.md)
- **项目约定文件 GEMINI.md**：与 AGENTS.md / CLAUDE.md 同构——三个主流产品收敛到同一模式，值得直接照搬成租户级配置层。
  来源：[ai-coding-guide · gemini-cli](https://github.com/jnMetaCode/ai-coding-guide/blob/main/gemini-cli/README.md)

> 待核实：headless/non-interactive 模式的准确参数名、逐工具确认与白名单配置的具体键名、是否内建子代理与上下文压缩。

## Goose

Block 开源的通用 agent（不只写代码），提供桌面 App 与 CLI 两种前端，能力靠 extension 拼装；`goose run` 这类命令使其可被脚本与流水线直接调用。
（来源：[Command-Line Interface · block/goose · DeepWiki](https://deepwiki.com/block/goose/3.2-command-line-interface)、[Landscape Profile · Goose (Block)](https://mrpeppersdev.github.io/agent-infrastructure-landscape/systems/goose-block--block-github-io)）

- **recipe 是这个产品最有借鉴价值的一层**：把一次会话的指令、工具集、模型与参数固化成可复用的 YAML（会话可「存为 recipe」再重放），于是「任务定义」成了版本化、可评审的**数据**而不是散落的聊天记录。
  来源：[Reusable Recipes · goose docs](https://block.github.io/goose/docs/guides/recipes/session-recipes/)、[Recipes · Goose](https://mintlify.wiki/block/goose/guides/recipes)
- **extension 统一走 MCP 协议**：工具不是写死在 harness 里，而是安装式能力；配合 recipe 可以精确指定「这次任务允许哪些工具」。
  来源：[Recipes · Goose](https://mintlify.wiki/block/goose/guides/recipes)
- **CLI + 桌面双前端共享同一 agent 内核**：同一套能力既有人在环里的 GUI，也有可自动化的命令行入口——对 SaaS 产品而言，这意味着 API/后台任务与前端交互可以复用同一个执行引擎。
  来源：[Command-Line Interface · DeepWiki](https://deepwiki.com/block/goose/3.2-command-line-interface)

> 待核实：审批/权限模式的具体档位、`goose run` 的准确参数与是否支持会话恢复、上下文压缩与子代理能力。

## 对 beauty-moon 的可借鉴结论

> 对照 beauty-moon 现状（已有 run 状态机、事件溯源、工具注册表、MCP client+server、Skills、只读代码工具、jobs 队列；缺 sandbox、上下文压缩、子代理、评测埋点）。

1. **沙箱要按「资源池 + 独立生命周期」设计，而不是给现有工具加个开关**。OpenHands 把 isolated runtime 做成可复用、可回收的一等对象，Codex 则是「沙箱内自动执行、越界才审批」。beauty-moon 缺 sandbox，建议把 workspace-service 的沙箱生命周期阶段 B 直接对齐成「每次 run 一个 runtime、带租户隔离与配额」，并把工具注册表里的执行类工具统一声明 sandbox_required，让权限裁决从「逐调用点确认」上移为策略层。
   来源：[Agent Canvas Architecture · OpenHands](https://docs.openhands.dev/openhands/usage/agent-canvas/architecture)、[Agent SDK 概览 · Claude Code](https://code.claude.com/docs/zh-CN/agent-sdk/overview)
2. **上下文压缩必须建立在「结构化索引」上，而不是首尾截断**。Aider 的 repo map（tree-sitter 符号图）证明小 token 预算也能保留全局感知；Claude Code 则用「项目约定文件（持久）+ 自动压缩（临时）」两层分工。beauty-moon 已有事件溯源，天然是一份完整 transcript——压缩策略可以做成「事件流 → 摘要事件」的确定性投影，并把摘要本身也落成事件（可回放、可评测）。
   来源：[ai-code-editor-almanac · Aider](https://github.com/ArdurAI/ai-code-editor-almanac/blob/main/tools/aider.md)
3. **子代理先按「同一 run 内的受控子任务」落地，别上通用多代理编排**。Codex 社区的 codex exec 嵌套用法与 OpenHands 的 scoped agent 都是同一个思路：收窄工具与上下文再委派。beauty-moon 的 run 状态机可直接加一个子 run（parent_run_id + 继承的租户与配额），事件流父子可追溯，比引入新框架成本低得多。
   来源：[codex-cli-subagent](https://lobehub.com/skills/timurkhakhalev-cc-plugins-codex-cli-subagent)、[OpenHands in September](https://www.openhands.dev/blog/new-in-agent-canvas-september-2026)
4. **把「任务定义」固化成 recipe 形态是可复用性的关键一跳**。Goose 的 recipe（指令 + 工具集 + 模型 + 参数，YAML 化、会话可存为 recipe 再重放）正好补 beauty-moon 目前偏「一次性对话」的短板；配合已有的 Skills，可以形成「Skill = 能力，Recipe = 一次受控执行契约」的分层。
   来源：[Reusable Recipes · goose docs](https://block.github.io/goose/docs/guides/recipes/session-recipes/)
5. **评测埋点应直接挂在事件溯源上，而不是另建一套日志**。业界讨论的共同要求是「出问题时要能还原当时用的是哪个模型、调了哪些工具、用了哪个 Skill、配置是什么版本」；beauty-moon 的事件流已具备原料，建议补齐「模型版本 / 工具调用 / Skill 命中 / 压缩前后 token / 审批路径」这几类字段并把它们定义为评测基线指标，否则子代理与压缩上线后无法判断优劣。
   来源：[Agent Harness 演进：六级阶梯](https://cloud.tencent.cn/developer/article/2749187)、[如何构建 Agent Harness（四）：受控执行](https://java.agentscope.io/v2/zh/blogs/how-to-build-agent-harness/04-controlled-execution)

> 本节的具体产品事实均引自上方来源标题与摘要；标「待核实」的细节不作为结论依据。
