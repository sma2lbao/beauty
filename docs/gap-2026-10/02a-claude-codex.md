# 02a · Claude Code 与 OpenAI Codex 横评

> 调研范围：Claude Code 与 OpenAI Codex 的产品形态、工具/执行、权限与沙箱、子代理、上下文与记忆、扩展机制、headless/SDK。
> 纪律：只取搜索结果标题+摘要，未确认处标「待核实」。
>
> **2026-10-25 复核**：Claude Code / Codex 的机制结论无实质变化（自动压缩、分层权限、子代理、SDK 均仍成立），本文继续有效；变化只在对比对象名单（Gemini CLI 已被 Antigravity CLI 取代，见 02b）。

## 总览矩阵

| 产品         | 形态             | 工具与执行                    | 权限/沙箱                        | 子代理/并行                | 上下文与记忆       | 扩展机制              | headless/SDK       | 亮点机制                   |
| ------------ | ---------------- | ----------------------------- | -------------------------------- | -------------------------- | ------------------ | --------------------- | ------------------ | -------------------------- |
| Claude Code  | CLI+IDE+Web/SDK  | 内置 Bash/读写/搜索工具       | 分级审批+allow/deny 规则         | 内置 Task 子代理，可并行   | CLAUDE.md+自动压缩 | MCP/Skills/Hooks/命令 | Agent SDK+headless | 权限模式与钩子生态         |
| OpenAI Codex | CLI+IDE+云端/App | shell 执行+文件编辑等本地工具 | OS 级沙箱+审批策略（待核实细节） | 嵌套 codex exec 可作子代理 | AGENTS.md+会话历史 | MCP/配置化 profile    | codex exec + SDK   | 沙箱内自动执行、边界外审批 |

## Claude Code

定位是「终端里的 agent harness + 可嵌入的 Agent SDK」：官方文档明确以 Agent SDK 形式提供同一套 agent 循环（内置工具、上下文管理、权限），headless 场景可编程调用。
（来源：[Agent SDK 概览 · Claude Code Docs](https://code.claude.com/docs/zh-CN/agent-sdk/overview)、[Agent SDK overview](https://code.claude.com/docs/en/agent-sdk)）

- **权限不是单点确认，而是分层规则**：工具调用按 allow/deny/ask 规则裁决，配合 Hooks 在工作流节点插入确定性的前置校验，把「模型自觉」换成「策略强制」。
  来源：[Agent SDK 概览](https://code.claude.com/docs/zh-CN/agent-sdk/overview)
- **子代理（Task 工具）是一等公民**：主代理把子任务派给独立上下文的子代理并取回结论，既降低主上下文膨胀，也天然支持并行的只读探索。
  来源：[Agent SDK overview](https://code.claude.com/docs/en/agent-sdk)
- **记忆分两层**：持久层是 CLAUDE.md 这类项目约定文件（随仓库走、可评审），临时层是会话内上下文与自动压缩；扩展层是 MCP server / Skills / 斜杠命令，全部是文件与协议，不需要改 harness 本体。
  来源：[Agent SDK 概览](https://code.claude.com/docs/zh-CN/agent-sdk/overview)

> 待核实：自动压缩的确切触发阈值、子代理并发上限、各权限模式的精确语义（搜索结果未给出具体数值，不臆造）。

## OpenAI Codex

形态覆盖 CLI、IDE 扩展与云端排障/委派三种入口，共用一套「读仓库 → 提议改动 → 执行验证」的循环；CLI 侧以本地进程 + 沙箱执行命令，官方培训材料把它定位为开发者工具链的一部分。
（来源：[OpenAI Codex Introduction · RX-M](https://rx-m.com/training/openai-codex-introduction/)、[Codex 102: Practical Workflows · OpenAI Academy](https://academy.openai.com/public/clubs/builders-etkn1/resources/codex-102-practical-workflows-2026-03-18)）

- **权限模型是「沙箱 + 审批策略」两件套，而非逐个工具确认**：可自动执行的工作限制在沙箱边界内（可写范围、可联网与否），越界动作才升级到人工审批。这比逐调用确认更少打断、也更难被绕过。
  来源：[ai-coding-guide · Codex 说明](https://github.com/jnMetaCode/ai-coding-guide/blob/main/codex/README.md)
- **headless 入口是 `codex exec`**：一次性执行、可脚本化嵌入 CI 与其他 agent；社区正是用它把 Codex 当「嵌套子代理」调用（在同一个项目目录里起一个聚焦的子进程），这是一种不依赖框架内建多代理的低成本并行/分工方式。
  来源：[codex-cli-subagent · LobeHub](https://lobehub.com/skills/timurkhakhalev-cc-plugins-codex-cli-subagent)
- **项目约定用 AGENTS.md 承载**：与 CLAUDE.md 同构，说明「仓库级说明书」已是业界共识格式，对多租户产品意味着可以把它做成租户/工作区级配置的一等公民。
  来源：[ai-coding-guide · Codex 说明](https://github.com/jnMetaCode/ai-coding-guide/blob/main/codex/README.md)

> 待核实：沙箱的具体实现（seatbelt/landlock 之类）、审批模式的准确名称与档位、Codex 原生是否提供内建子代理（上述子代理用法来自社区扩展而非官方内建）。
