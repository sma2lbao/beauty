# 08c · 前端/交互审计（apps/web、packages/console-ui、apps/desktop）

> 审计员：frontend-auditor（只读）。任务：task-3。日期：2026-10-27。

## 0. 方法与时效

- **时效**：仓库 HEAD `84e478a`（2026-10-26 23:11 +08:00，BYOK 全链路）。审计时间 2026-10-27 21:00 前后（Asia/Shanghai）。对照基线 `docs/gap-2026-10/00-summary.md`（2026-10-25 复核）与 `docs/gap-2026-10/06-refresh-2026-10-25.md`。
- **方法**：只读源码 + 定向 grep；未启动应用、未跑测试、未构建（保持只读，不产生生成物）。所有 `file:line` 均来自本次实际读到的代码，未读过的组件不作判断。
- **判定口径**：✅ 有实现且入口可达；⚠️ 有实现但能力/入口残缺或与说法不符；❌ 未找到实现（以指定目录内 grep 零命中为证据）。
- **范围**：`apps/web/src`（pages / features / components / lib / stores）、`packages/console-ui/src`、`apps/desktop/src`（main / preload / renderer / shared）；`packages/design` 仅作交叉验证。
- **局限**：无运行时验证（无截图、无交互录制），“用户感知”结论由代码路径与文案推断；`docs/gap-2026-10/drafts/` 本次新建。
- **与本报告的基线差异**：`00-summary.md:55` 称交互面“Markdown/Diff/产物预览/run 看板/计划编辑/通知中心 ✅”——计划编辑只在 `/runs` 抽屉里成立，会话内不成立（见 §1.1 #5）；`06-refresh-2026-10-25.md:110` 称“终端面板命名”仍部分成立（见 §1.2 #4）。

## 1. 现状总表

### 1.1 会话工作台（apps/web 工作台 `/`）

| 能力                         | 状态         | 证据                                                                                                                                                  | 备注                                                                                            |
| ---------------------------- | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| SSE 流式渲染 meta/delta/done | ✅           | `apps/web/src/features/chat/use-chat-stream.ts:195-231`；`apps/web/src/lib/sse.ts:136-164`                                                            | fetch+ReadableStream 手写解析（带 Bearer/CSRF，故非 EventSource）；正文边产出边渲染             |
| 工具调用卡片                 | ✅           | `apps/web/src/components/console/tool-call-card.tsx:89-93`、`:115-127`、`:171-201`                                                                    | 风险徽标/决策/耗时/错误码/输出/sha256/路径/unified diff 都在卡里，信息密度高                    |
| 审批/确认就地交互            | ✅           | `apps/web/src/features/chat/approval-block.tsx:11-63`、`:68-101`；`message-list.tsx:308-325`                                                          | 放行执行 / 拒绝计划（带原因）/ 确认危险操作，不跳页                                             |
| ask_question 提问卡          | ✅           | `apps/web/src/features/chat/question-card.tsx:136-289`                                                                                                | 多题/单选多选/自定义输入/截止时间/提交后锁定态；问题按纯文本渲染（`:21-23` 注释），最完整的一块 |
| 计划展示                     | ✅           | `approval-block.tsx:11-63`（Markdown 只读）                                                                                                           | —                                                                                               |
| 计划编辑                     | ❌（会话内） | 会话内只有两个按钮 `approval-block.tsx:38-53`；编辑能力只在 `apps/web/src/components/console/run-detail.tsx:329-352`                                  | 想改计划必须离开会话去 /runs，违反“就地交互”心智                                                |
| 失败重试                     | ❌（会话内） | `message-list.tsx:338-345` 只显示 status_reason + “去任务编排”；`:361-372`                                                                            | /runs 抽屉有“重试（另起一跑）”：`run-detail.tsx:269-278`                                        |
| 消息重发/编辑/重新生成       | ❌           | `message-list.tsx:414-556` 历史消息只有文本/时间/附件/MCP chips，无操作菜单                                                                           | 错了只能重新打字                                                                                |
| 停止生成                     | ⚠️           | `composer.tsx:208-216`（仅 SSE 期间 abort）；任务的“暂停执行”是 disabled：`approval-block.tsx:153-163`（title=“暂停能力暂未开放”）                    | 用户看到按钮却点不动，比没有更伤信任                                                            |
| 断线重连（会话流）           | ⚠️           | 续传基座具备：`sse.ts:115-116`、`:145-147`；但 `use-chat-stream.ts:161-276` 全程未传 `lastEventId`，流断只 `onInterrupted`→报错并恢复草稿             | runs 侧已完整（`use-run-events.ts:42`、`:56-76`）                                               |
| 多轮上下文提示               | ❌           | 仅有 `composer.tsx:201-205`“任务进行中 · 发送会并入追加指令”；无 token 水位/上下文用量                                                                | `status-bar.tsx:1-4` 注释自认“没有 token 计量接口就不摆假数字”                                  |
| 会话列表/搜索/重命名/删除    | ✅           | `features/sidebar/session-list.tsx:38-44`（搜索）、`session-row.tsx:61-88`（就地重命名）、`:113-164`（菜单/确认删除）、`use-sidebar-model.ts:197-203` | 另有归档/取消归档（`session-row.tsx:143-156`）、未分组收容（`session-list.tsx:170-209`）        |
| 工作区切换器                 | ✅           | `components/console/workspace-switcher.tsx:147-149`、`:177`、`:206-227`（添加/管理/沙盒边界提示）；`pages/workspace.tsx:74-88`                        | URL `?workspace=<id>` 为唯一真源                                                                |
| 附件上传/下载                | ⚠️           | 上传：`use-chat-stream.ts:142-145`（5 个/单文件 10MB）；下载：`message-list.tsx:478-490`（blob+a.download，失败静默）                                 | 无内联缩略图/预览；pdf/图片都只能下载                                                           |
| 发送方式/技能/MCP 选择       | ✅           | `composer.tsx:142-153`（自动/对话/任务）、`:160-173`、`:180-200`                                                                                      | 会话内可换技能/MCP，但换不了模型                                                                |
| 消息复制                     | ❌           | 无按钮（`message-list.tsx:414-556`）；复制能力只有产物正文（`changes.tsx:366-378`）与 API Key（`api-keys-panel.tsx:108-134`）                         | agent 产品里“复制代码/复制回答”是最高频动作之一                                                 |

### 1.2 Agent-native 控件

| 能力                        | 状态      | 证据                                                                                                                                           | 备注                                                                                                                                     |
| --------------------------- | --------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------- |
| todo / 任务清单             | ❌        | `grep -i todo apps/web/src` 零命中                                                                                                             | 模型不维护清单，用户看不到“还剩几件事”                                                                                                   |
| 文件树                      | ❌（web） | `grep FileTree\|文件树 apps/web/src` 零命中                                                                                                    | 桌面端有：`apps/desktop/src/renderer/pages/files-page.tsx:142-254`（懒加载目录树）                                                       |
| 文件预览                    | ⚠️        | web 只能看“产物正文”：`artifact-preview.tsx:28-77`；桌面端 `files-page.tsx:323-343` 纯 `<pre>`，无高亮/无行号/只读                             | 没有“文件系统”概念，只有“产物列表”                                                                                                       |
| 终端面板                    | ❌        | 页签叫“终端”但内容是事件流水：`execution-panel.tsx:208-267`；无命令输入、无 PTY                                                                | 桌面端更典型：IPC 有 `shell.run`（`contract.ts:159-162`、`main/service.ts:146`），**渲染层零调用**（仅 `test-utils/fake-bridge.ts:225`） |
| @ 引用（文件/知识/产物）    | ❌        | `composer.tsx:117-205` 只有附件/发送方式/技能/MCP；无 mention 解析                                                                             | 无法把“这个文件/这篇文档”指给模型                                                                                                        |
| 斜杠命令                    | ❌        | `grep keydown` 仅 6 处且全为 Escape 关闭（`workspace-actions.tsx:56-59`、`workspace-switcher.tsx:121-124`、`notification-center.tsx:115-118`） | 无 /clear、/plan、/model 之类                                                                                                            |
| 代码高亮                    | ❌        | `packages/console-ui/src/markdown.tsx:78-92` 的 `code`/`pre` 只有底色；`apps/web/package.json` 无 highlight 依赖；`grep highlight` 零命中      | 长代码块是纯灰底等宽文本                                                                                                                 |
| 代码/输出复制               | ⚠️        | 代码块与工具输出均无复制按钮：`markdown.tsx:88-92`、`tool-call-card.tsx:171-201`                                                               | 只有产物正文与 API Key 有复制                                                                                                            |
| Diff 查看器                 | ✅        | `changes.tsx:319-325`（inline/split + 双行号）；`console-ui/src/diff-view.tsx:35-67`、`:69-120`、`:152-171`（解析失败回退代码预览，不伪造）    | 工具卡也内联 DiffRows（`tool-call-card.tsx:195-197`）                                                                                    |
| 产物预览 图片/文本/Markdown | ✅        | `artifact-preview.tsx:28-62`；图片走 blob URL（`changes.tsx:84-90`）                                                                           | —                                                                                                                                        |
| 产物预览 PDF/HTML           | ❌        | `artifact-preview.tsx:6`、`:80-92`“该文件不支持在线预览，请下载后查看”                                                                         | HTML 产物连沙箱内预览都没有                                                                                                              |
| Markdown 渲染与安全         | ✅        | `console-ui/src/markdown.tsx:12-15`（rehype-sanitize 默认 schema、不启用 raw HTML）、`:58-77`（链接白名单 + noopener）                         | `:107-114` 的 `img` 无域名白名单，属残余风险（图片可被用来打点/追踪）                                                                    |
| 工具重新执行                | ❌        | `tool-call-card.tsx` 无重跑按钮                                                                                                                | 失败后只能让模型自己再来一次                                                                                                             |

### 1.3 模型与设置

| 能力                       | 状态 | 证据                                                                                                                                                                                                                                                                                                       | 备注                                                                                    |
| -------------------------- | ---- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| 模型选择                   | ❌   | `components/console/topbar.tsx:1-6` 注释明说“没有逐会话换模型的接口，不伪造下拉能力”；`:118-133` 只读 span 显示 `default_model`                                                                                                                                                                            | 与 `00-summary.md:88`（P1-10）一致，仍未落地                                            |
| 模型参数（temperature 等） | ❌   | 前端无任何参数表单；`byok-panel.tsx` 只有 provider+key                                                                                                                                                                                                                                                     | —                                                                                       |
| BYOK                       | ✅   | `byok-panel.tsx:57-59`（密文存储/尾号掩码）、`:63-119`（openai/anthropic/google + 删除）                                                                                                                                                                                                                   | 租户级，用在 `/settings`                                                                |
| 用量展示                   | ✅   | `pages/usage.tsx:36-84`（今日/本月 tokens 与请求）、`:86-121`（按模型）                                                                                                                                                                                                                                    | 无配额上限/剩余百分比/成本金额                                                          |
| 通知中心                   | ✅   | `notification-center.tsx:132-181`（未读角标/全部已读/点击跳转）、`run-status-watcher.ts:36-55`（7 类状态→文案）                                                                                                                                                                                            | 站内 + toast；**无偏好设置、无浏览器桌面通知**（`grep Notification.permission` 零命中） |
| 主题（亮/暗）              | ❌   | `grep prefers-color-scheme` 或 `toggleTheme` 零命中                                                                                                                                                                                                                                                        | 只有暗色 `--ha-*` 一套                                                                  |
| i18n                       | ❌   | `grep i18n` 或 `useTranslation` 零命中                                                                                                                                                                                                                                                                     | 全部硬编码中文                                                                          |
| 快捷键                     | ❌   | 只有 Enter 发送 / Shift+Enter 换行（`composer.tsx:93-102`）；无全局键位、无命令面板（⌘K）                                                                                                                                                                                                                  | —                                                                                       |
| 可访问性                   | ⚠️   | 正向：`question-card.tsx:136`（section aria-label）、`:209-224`（label htmlFor）、`sidebar.tsx:37`（aside aria-label）、`workspace-switcher.tsx:147-149`；负向：无 axe/jest-axe（grep 零命中）、简单对话的流式正文无 live region（`message-list.tsx:240-249`，只有任务实时正文 `:255-273` 有 role=status） | 键盘可达性基本靠原生控件，无焦点陷阱、无跳转链接                                        |
| 移动端响应式               | ⚠️   | 侧栏 `<lg` 整块消失（`sidebar.tsx:37-40`），移动端只有导航条（`console-layout.tsx:63-79`，9 项无“会话”），**会话列表无替代入口**；执行面板 `<xl` 消失（`execution-panel.tsx:57`）；`/changes` 产物列表 `<md` 消失（`changes.tsx:194`）                                                                     | 手机上只能看，换不了会话、选不了产物                                                    |
| 设置页                     | ✅   | `pages/settings.tsx:33-77`（组织/邀请/成员/多端会话/BYOK/API Key/OAuth 应用）                                                                                                                                                                                                                              | 无“偏好”分区（主题/语言/通知/快捷键）                                                   |

### 1.4 任务编排与运维页

| 能力            | 状态 | 证据                                                                                                                                      | 备注                                                                        |
| --------------- | ---- | ----------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------- |
| /runs 看板      | ✅   | `pages/runs.tsx:18`（四列）、`:178`（横向滚动）、`:417-455`（卡片状态行）                                                                 | 5s 轮询（`:58-62`），非 SSE；无搜索/分页/批量操作                           |
| 详情抽屉        | ✅   | `run-detail.tsx:143-146`（aside，移动端全屏）                                                                                             | 人工边界控制台：规划/放行/拒绝/确认/重试/取消/resume/追加指令/交付物/事件流 |
| 步骤/事件时间线 | ⚠️   | `run-step-timeline.tsx:1-7` 注释：“步骤不进 SSE，是轮询快照；执行中节点要等一次 invoke 结束才出终值”；轮询 1.5s（`run-detail.tsx:78-89`） | 与 ChatGPT/Cline 的“逐字逐工具实时”观感有差距                               |
| 取消/重试/恢复  | ✅   | `run-detail.tsx:233-242`（resume）、`:269-278`（retry）、`:290-299`（cancel）；CANCELLABLE 与后端 TRANSITIONS 对齐（`:33-42`）            | —                                                                           |
| 计划编辑        | ✅   | `run-detail.tsx:329-335`（textarea aria-label=“计划内容”）、`:341-352`（保存 savePlan）                                                   | 只在 /runs 里                                                               |
| 追加指令        | ✅   | `run-detail.tsx:375-379`（说明并入人工边界）、`:370-428`                                                                                  | —                                                                           |
| /changes        | ⚠️   | 产物 + Diff + 复制 + 下载（`changes.tsx:268-273`、`:319-325`、`:366-387`）；“提交评审”只是 `Link to="/runs"`（`:144-149`）                | 不是评审流；移动端看不到产物列表（`:194`）                                  |
| /outbox         | ✅   | `outbox.tsx:104-117`（统计卡）、`:130-149`（过滤）、`:150-168`（批量重放）、`:205-215`（单条重放）                                        | 运维面完整                                                                  |

### 1.5 桌面端（apps/desktop 本地电脑模式）

| 能力          | 状态              | 证据                                                                                                              | 备注                                                                  |
| ------------- | ----------------- | ----------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| 本地能力边界  | ✅                | `apps/desktop/README.md:81-89`（contextIsolation/sandbox、路径逃逸拒绝、256KB 读取上限、120s 命令超时、输出截断） | 边界写得清楚                                                          |
| 模型配置      | ✅                | `renderer/pages/settings-page.tsx:88-142`（baseUrl/model/apiKey）                                                 | 无 temperature、无模型列表、无连通性测试                              |
| 本地工具      | ✅                | `main/local/agent.ts:27`（12 轮上限）、`:655-683`（list_files/read_file/write_file/run_command）、`:729-747`      | 无 MCP/技能/审批（README.md:113 自认）                                |
| 云/本地切换   | ✅                | `renderer/components/sidebar.tsx:90-107`（segmented）、`shell.tsx:43-47`、`pages/cloud-page.tsx:62-82`            | 云端只经系统浏览器打开，不内嵌（README.md:112）                       |
| 流式渲染      | ✅                | `main/local/agent.ts:443-463`（assistant.delta）、`renderer/components/message-stream.tsx:79-87`（尾部光标）      | 事件经 `agent.subscribe` 推送（`contract.ts:88-105`）                 |
| 停止执行      | ✅                | `renderer/components/composer.tsx:73-78`（停止按钮）、`agent.ts:392-411`（cancelled 收敛）                        | 运行中禁用发送（`:22`），无追加指令                                   |
| Markdown 渲染 | ❌                | `grep Markdown apps/desktop/src` 零命中；`message-stream.tsx:77-88` 纯 `whitespace-pre-wrap`                      | 模型答的代码块在桌面端就是一段纯文本——桌面端最刺眼的落差              |
| 审批流        | ❌                | `agent.ts:729-747` `write_file`/`run_command` 直接执行，无确认门                                                  | 契约注释写“危险操作由 UI 二次确认”（`contract.ts:160`），但 UI 不存在 |
| 终端 UI       | ❌                | `contract.ts:159-162`、`main/service.ts:146` 有 `shell.run`；渲染层零调用                                         | 能力有、入口无                                                        |
| Diff          | ❌                | `grep Diff apps/desktop/src` 零命中                                                                               | 写文件后无法看“改了什么”                                              |
| 会话管理      | ✅（无搜索/归档） | `sidebar.tsx:181-217`（就地重命名）、`:245-331`（菜单 + 二次确认删除）                                            | 会话多时没有搜索                                                      |
| 错误反馈      | ✅                | `chat-page.tsx:111-118`（role=alert 运行失败）、`tool-message-card.tsx:42-106`（工具卡折叠输出）                  | —                                                                     |

### 1.6 工程面

| 能力         | 状态 | 证据                                                                                                                                                                                    | 备注                                                                                              |
| ------------ | ---- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------- |
| 前端测试覆盖 | ✅   | web 40 个 test 文件（`apps/web/src/**/*.test.tsx` 实测清单）；阈值 `vitest.config.mts:26-47`（web 84/78/74/82，console-ui 85/66/75/84，desktop renderer 83/80/70/79、node 83/79/63/76） | 组件与实现基本成对（chat-stream / chat-approval / question-card / diff-view / markdown 都有测试） |
| 错误边界     | ❌   | `app.tsx:20-58` 只有 lazy+Suspense fallback；`main.tsx:19-26` 用 `BrowserRouter`（非 data router，无 errorElement）                                                                     | 任一页渲染异常即白屏                                                                              |
| 性能/长列表  | ⚠️   | `message-list.tsx:184-199` 全量 map；`apps/web/package.json` 无虚拟化依赖                                                                                                               | 长会话 + 事件流水会一起变慢                                                                       |
| 离线/弱网    | ⚠️   | runs SSE 指数退避（`use-run-events.ts:42`）；无 `navigator.onLine`/离线横幅（grep 零命中）                                                                                              | 断网时用户只看到请求错误文案                                                                      |
| 埋点/监控    | ❌   | `grep analytics` 或 `sentry` 零命中                                                                                                                                                     | 交互改进无法用数据验收                                                                            |

## 2. 与主流 agent 产品交互形态的差距

**Cline（IDE 侧边栏）**：每一步工具调用都能展开成“读哪个文件 / 改哪几行”的可视 diff，改动前有 Approve/Reject，任务清单（todo）常驻，文件与终端就在同一屏。
→ beauty moon 的工具卡信息密度够（risk/decision/sha256/diff 都有，`tool-call-card.tsx:171-201`），但**没有“文件系统”这一层**：用户看到“写入了 a.md”，却无法在会话里绕到那个文件、翻到那一行、看合并后的结果；计划要改还得离开会话。用户感知是“审批工单”，不是“和 agent 一起改东西”。

**Claude Code（终端 TUI）**：斜杠命令、/plan 模式、todo 清单、Esc 中断、上下键回改上一条、checkpoint 回滚、模型可切换。
→ beauty moon 会话里这些一个都没有：无斜杠命令（§1.2）、无 todo、无中断（任务“暂停执行”是 disabled，`approval-block.tsx:153-163`）、无消息编辑重发（`message-list.tsx:414-556`）、无模型选择（`topbar.tsx:1-6`）。用户感知是“发出去就只能等”。

**ChatGPT（Web/桌面）**：流式、复制、重新生成、编辑上一条、模型下拉、临时会话、通知与快捷键、图片/文件预览。
→ beauty moon 的流式与审批更强（ChatGPT 没有工具审批），但**基础对话动作缺一半**：无复制、无重新生成、无编辑重发；模型是只读标签。用户感知是“这个产品的对话不是我的，是系统的”。

**Goose 桌面端（本地 agent）**：文件树 + 改动 diff + 扩展/MCP + 会话内工具卡 + 权限确认。
→ beauty moon 桌面端有正确骨架（工作区守卫、4 工具、流式、停止），但**没有 Markdown、没有 diff、没有审批、没有终端 UI**（§1.5）：答出来的代码是纯文本，写文件不经确认，`shell.run` 有能力却无入口。用户感知是“能干的 demo”，不是“敢交给它干活的工具”。

**一句话结论**：现在的交互形态是“**run 控制台 + 一个消息列表**”，主流 agent 产品是“**一个能看见、能随时插手的执行现场**”。差别不在功能多少，而在“用户能否在会话里完成闭环”——beauty moon 的闭环被拆到了 `/runs`（`message-list.tsx:338-372` 只给外链）。

## 3. 交互缺口清单

### P0（直接决定“像不像 agent 产品”）

1. **会话内没有执行现场（文件树/终端）**。场景：agent 说“已写入 src/a.ts”，用户想在会话里点开看改了什么——现在只能去 /changes 翻产物，或去 /runs 看事件流。证据：终端页签是事件流水（`execution-panel.tsx:208-267`）、无文件树（grep 零命中）、工具卡只显示路径前 3 条（`tool-call-card.tsx:171-190`）。
   **→ ✅ 已落地（2026-10-27）**：右侧面板改为「执行步骤 / 文件 / 终端」，文件树走 workspace-service 新增的 `GET /v1/sandboxes/{id}/tree`，点工具卡路径或树节点在会话内打开查看器（「内容」带行号 / 「改动」由 write·edit 参数重建补丁），终端在沙箱里跑真实命令。2026-10-27 深夜升级：补丁改由服务端 `tool_calls.patch` 与跨 run 聚合端点提供（`apps/web/src/lib/tool-calls-api.ts`，降级回退本地重建），文件树显示会话级 `+N/−M`、查看器加「跳到上次修改」，终端改为 PTY 持久 shell 会话（SSE + `Last-Event-ID` 续传）。剩余限制：补丁仅 write/edit、只取首页 200 条、终端只清洗 ANSI（无 xterm 全屏 TUI）、会话表进程内单实例。
2. **计划只能放行/拒绝，不能改**。场景：计划第 2 步方向不对，用户想改一行再放行——会话里做不到，必须跳到 /runs 抽屉编辑（`run-detail.tsx:329-352`）。证据：`approval-block.tsx:38-53` 仅两按钮。
3. **失败后会话内无重试、无“重发/编辑重发”**。场景：模型答歪了或网络抖了，用户想“这条重来”或“改一句再发”——只能去 /runs 或重新打一遍。证据：`message-list.tsx:338-345`、`:361-372`；`use-chat-stream.ts:264-271` 只恢复草稿。
4. **模型不可选、参数不可调**。场景：简单问答想换便宜模型、写代码想换强模型——没有入口。证据：`topbar.tsx:1-6`、`:118-133`；`byok-panel.tsx` 只有租户级 key。
5. **没有消息级复制/重新生成/引用**。场景：抄一段命令、让回答再生成一版、引用上一条——全无。证据：`message-list.tsx:414-556` 无操作菜单；代码块无复制（`markdown.tsx:88-92`）。

### P1（缺了会被评价为“半成品”）

6. **无 todo/任务清单**：长任务看不到进度分解（grep 零命中）。
7. **无 @ 引用、无斜杠命令**：无法把文件/知识/产物指给模型，也无快捷入口（`composer.tsx:117-205`；`grep keydown` 仅 Escape）。
8. **无代码高亮**：读代码场景体验差（`markdown.tsx:78-92`；无 highlight 依赖）。
9. **无上下文水位**：用户不知道离压缩/丢历史还有多远（grep 零命中；`status-bar.tsx:1-4` 自认）。
10. **会话流无断线续传**：弱网下流一断就报错，不自动带 Last-Event-ID 重连（`use-chat-stream.ts:161-276`；基座 `sse.ts:145-147` 没被用）。
11. **移动端不可用**：`<lg` 无侧栏且无会话列表替代（`sidebar.tsx:37-40`、`console-layout.tsx:63-79`）；`<xl` 执行面板消失（`execution-panel.tsx:57`）；`<md` 产物列表消失（`changes.tsx:194`）。
12. **“停止”名不符实**：任务中“暂停执行”是 disabled（`approval-block.tsx:153-163`），用户会反复点。
13. **无错误边界**：任一页抛错即白屏（`app.tsx` 无 ErrorBoundary；`main.tsx:22` BrowserRouter）。
14. **桌面端无 Markdown/无 diff/无审批**：代码纯文本、写文件不确认（`message-stream.tsx:77-88`；`agent.ts:729-747`）。
15. **桌面端终端有 IPC 无 UI**：`shell.run` 悬空（`contract.ts:159-162`、`main/service.ts:146`）。
16. **通知无偏好、无桌面通知**：只能站内看，切走后收不到（`grep Notification.permission` 零命中）。
17. **用量无配额/成本**：只有 tokens 绝对值（`usage.tsx:17-19`、`:36-84`）。
18. **/runs 5s 轮询且无搜索/分页**：run 多了找不到（`runs.tsx:58-62`）。

### P2（打磨项）

19. 无埋点/前端监控（grep 零命中）——交互改动无法验收。
20. 无全局快捷键/命令面板（`grep keydown` 仅 Escape 三处）。
21. 无长列表虚拟化（`message-list.tsx:184-199`；无对应依赖）。
22. 无离线提示（`grep navigator.onLine` 零命中）。
23. a11y 无自动化门禁（无 axe）；简单对话流式正文无 live region（`message-list.tsx:240-249`）。
24. 附件下载失败静默（`message-list.tsx:478-490`）；剪贴板失败静默（`changes.tsx:101-109`、`topbar.tsx:49-59`）。
25. `/changes` 的“提交评审”是链接不是流（`changes.tsx:144-149`）。
26. Markdown `img` 无域名白名单（`console-ui/src/markdown.tsx:107-114`）。
27. 桌面端会话无搜索/无归档（`sidebar.tsx:155-336`）。
28. 提问卡无倒计时跳动（`question-card.tsx:279-288` 只有截止时间文案）。

## 4. 证据索引

**会话工作台**

- `apps/web/src/features/chat/use-chat-stream.ts:161-276` 发送与流式全流程；`:142-145` 附件上限；`:195-231` meta/delta/done 处理；`:264-271` 失败恢复草稿；`:284` 停止=abort。
- `apps/web/src/features/chat/composer.tsx:88-109` 输入与 Enter 语义；`:117-205` 附件/发送方式/技能/MCP；`:201-205` inFlight 提示；`:208-216` 停止按钮；`:228-230` 操作提示。
- `apps/web/src/features/chat/message-list.tsx:88-98` 自动贴底；`:184-199` 历史消息渲染；`:240-273` 流式/liveRun；`:292-336` 工具卡/审批/提问；`:338-372` 终态与外链；`:414-556` MessageBlock；`:478-490` 附件下载。
- `apps/web/src/features/chat/approval-block.tsx:11-63` 计划卡；`:68-101` 危险确认；`:107-166` RunFooterAction（`:153-163` 暂停 disabled）。
- `apps/web/src/features/chat/question-card.tsx:21-23` 纯文本安全注释；`:136-289` 提问卡全量。
- `apps/web/src/lib/sse.ts:40-58`、`:68-100`、`:115-147` SSE 解析、中断语义、Last-Event-ID。
- `apps/web/src/components/console/execution-panel.tsx:57` 响应式断点；`:60-68` 三页签；`:208-267` “终端”=事件流水。
- `apps/web/src/components/console/tool-call-card.tsx:25-45` 风险/决策/sha256；`:171-201` 输出与 diff。

**agent-native**

- `packages/console-ui/src/markdown.tsx:12-15`、`:58-92`、`:107-114`、`:140-146` 渲染与安全策略。
- `packages/console-ui/src/diff-view.tsx:35-120`、`:152-171` inline/split/回退。
- `apps/web/src/components/console/artifact-preview.tsx:6`、`:28-92` 产物预览与不支持类型提示。

**会话列表/工作区/布局**

- `apps/web/src/features/sidebar/session-list.tsx:38-104` 搜索与空态；`:170-222` 未分组/加载更多。
- `apps/web/src/features/sidebar/session-row.tsx:61-164` 重命名/菜单/归档/删除。
- `apps/web/src/features/sidebar/use-sidebar-model.ts:64-68`、`:147-203` 轮询与三个 mutation。
- `apps/web/src/components/console/sidebar.tsx:37-81` 侧栏骨架。
- `apps/web/src/components/console/console-layout.tsx:30-40`、`:63-79` 移动端导航。
- `apps/web/src/components/console/workspace-switcher.tsx:147-227` 切换器与菜单。
- `apps/web/src/components/console/topbar.tsx:1-6`、`:63-133`、`:144-154` 面包屑/只读模型/分享。
- `apps/web/src/components/console/status-bar.tsx:1-4` 不摆假数据原则。
- `apps/web/src/stores/ui-store.ts:1-5`、`:33-36` 通知静音与持久化边界。
- `apps/web/src/pages/workspace.tsx:74-88` 工作区中心两列布局。

**编排与运维**

- `apps/web/src/components/console/run-detail.tsx:33-42`、`:143-146`、`:192-300`、`:319-353`、`:370-428`、`:432-455` 人工边界控制台。
- `apps/web/src/components/console/run-step-timeline.tsx:1-7`、`:39-42` 轮询快照。
- `apps/web/src/components/runs/use-run-events.ts:42`、`:56-76`、`:86-100` 断线重连。
- `apps/web/src/pages/runs.tsx:18`、`:58-62`、`:178`、`:246-256`、`:417-455` 看板/轮询/抽屉/卡片。
- `apps/web/src/pages/changes.tsx:144-149`、`:194`、`:268-325`、`:366-387` 产物评审页。
- `apps/web/src/pages/outbox.tsx:104-168`、`:205-215` 事件队列运维。

**设置/通知/用量**

- `apps/web/src/pages/settings.tsx:33-77` 设置分区；`apps/web/src/pages/usage.tsx:17-121` 用量。
- `apps/web/src/components/console/notification-center.tsx:98-181`；`toast-viewport.tsx:35-83`；`apps/web/src/lib/run-status-watcher.ts:36-55`、`:159-198`。
- `apps/web/src/components/console/byok-panel.tsx:57-119`；`api-keys-panel.tsx:22`、`:108-134`。

**壳层/工程**

- `apps/web/src/app.tsx:20-58`、`:79-102` 路由与 Suspense；`apps/web/src/main.tsx:19-26` QueryClient/BrowserRouter。
- `vitest.config.mts:26-47` 覆盖率阈值（唯一数字源）。

**桌面端**

- `apps/desktop/README.md:69-89`、`:106-113` 模型配置、安全边界、已知限制。
- `apps/desktop/src/shared/contract.ts:88-105`、`:112-122`、`:128-166` 事件/命令结果/桥接；`:159-162` shell.run。
- `apps/desktop/src/main/local/agent.ts:27`、`:392-411`、`:443-463`、`:655-683`、`:729-747` 本地循环与工具。
- `apps/desktop/src/renderer/pages/files-page.tsx:142-254`、`:323-343` 目录树与纯文本预览。
- `apps/desktop/src/renderer/components/message-stream.tsx:77-88` 纯文本正文；`tool-message-card.tsx:42-106` 工具卡。
- `apps/desktop/src/renderer/components/composer.tsx:22`、`:59-78` 运行中禁用发送/停止。
- `apps/desktop/src/renderer/components/sidebar.tsx:90-107`、`:155-336`、`:364-405` 模式切换/会话列表/工作区。
- `apps/desktop/src/renderer/pages/settings-page.tsx:88-160` 模型与云端地址。
- `apps/desktop/src/renderer/pages/cloud-page.tsx:1-5`、`:62-82` 云端只外链。
