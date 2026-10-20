# 功能差距与路线图（对比 DeepSeek Harness）

> 状态：**决策版**（2026-10-18）。已按本轮选型收敛：云 PG + 云 Redis（开发 SQLite/本地 Redis）、
> 对象存储暂缓、Temporal/E2B 暂缓、Logto 不用（自建 account-service）、Sentry 官方、Stripe/Resend/Langfuse 暂缓、
> Vercel 托管 Web、LLM 继续用 API。
> **当前阶段只要求本地可启动 + SaaS 部署；内网/离线部署暂缓（M0-13，不纳入 M0）。**

---

## 0. 判定口径

- 参照对象：`/Users/sma2lbao/Code/deepseek-harness`（本地优先 coding agent harness）；
- 参照范围：`packages/client/ui-*`（52 个 Web UI 模块）、`docs/tool-catalog.zh.md`（工具目录）、`docs/subsystems/*`；
- 标记：✅ 已有 ｜ ⚠️ 部分/可用但浅 ｜ ❌ 缺失 ｜ ⏸ 本轮暂缓 ｜ 🚫 不做；
- 里程碑：M0 平台基础 → M1 coding（受限）→ M2 office → M3 开放平台 → M4 规模化。

---

## 1. 现状总览

**已打通**

- account-service 自建 IdP（邮箱密码/刷新令牌/JWKS）、多租户；
- run 状态机、事件溯源、SSE 断线回放（后端 `Last-Event-ID`）、审批/确认/追加指令；
- 对话分流、多轮上下文、7 个办公工具、知识库、workspace 产物、outbox；
- Web 四个设计页面 + 知识库/设置/工作区/事件队列/官网；
- 开发默认 SQLite + `AUTO_CREATE_TABLES`；生产切云 PG；checkpointer 自动切换 PG/InMemory。

**结构性缺口**

1. **运行时**：工具硬编码；无 sandbox/终端/文件/代码工具（E2B 暂缓 → 执行类 coding 暂不可交付）；
2. **体验**：纯文本消息、附件/对象存储受限、无会话管理/搜索、产物预览受限、模型/权限/用量设置缺失；
3. **平台**：无 BFF/队列/限流/用量/开放 API/MCP，Vercel + 容器混合尚未落地。

---

## 2. Web 功能矩阵

| 能力                                     | 现状                | 目标                                     | 里程碑     | DSH 参照                  |
| ---------------------------------------- | ------------------- | ---------------------------------------- | ---------- | ------------------------- |
| Markdown/代码/表格渲染                   | ❌ 纯文本           | 消息/计划/产物统一渲染                   | M0         | ui-primitives             |
| 附件上传/预览                            | ❌                  | DB/本地文件 + 大小限制；对象存储后置     | M0（受限） | ui-attachment             |
| 消息操作（复制/重试/反馈）               | ❌                  | 复制/重试；👍👎 后置                     | M0/M4      | ui-message-feedback       |
| 会话管理（重命名/删除/归档/搜索/分页）   | ⚠️ 仅列表+新建      | 全套 + cursor 分页                       | M0         | ui-session / ui-workspace |
| 模型选择与 BYOK                          | ❌ 静态「自动路由」 | 模型列表 + 会话级选择 + BYOK             | M0/M3      | ui-model-selection        |
| @ 引用（文件/会话/知识库）               | ❌ 仅占位           | 只读引用/候选排序                        | M1         | ui-reference              |
| / 命令与命令面板                         | ❌                  | 会话级命令                               | M1         | ui-commands               |
| 计划/Todo/Goal                           | ⚠️ 仅审批时计划     | 常驻计划条 + Todo/Goal                   | M1         | ui-plan / ui-goal         |
| Agent 反问                               | ❌                  | ask_user_question                        | M1         | ui-user-questions         |
| 后台任务面板                             | ❌                  | 任务列表/终止（依赖 job 表）             | M1         | ui-jobs                   |
| 文件树 / 文件预览                        | ❌                  | 只读文件树 + 预览（不执行）              | M1         | ui-sidebar-files          |
| 终端面板                                 | ⏸                  | 依赖 sandbox 决策                        | M1（条件） | ui-sidebar-terminal       |
| Diff 评审                                | ⚠️ 统一 Diff        | 逐文件/行评论/应用                       | M1         | ui-deliverables           |
| 预览 URL                                 | ⏸                  | 依赖 sandbox 决策                        | M1（条件） | ui-sidebar-browser        |
| 产物卡片（图片/PDF/Office）              | ❌ 文本/Diff        | Markdown/图片先做；PDF/Office 待对象存储 | M0/M2      | ui-deliverables           |
| 执行轨迹                                 | ⚠️ 简化执行面板     | 完整轨迹 + 事件检索                      | M2         | ui-trajectory             |
| 子代理/Agent 团队                        | ❌                  | 子会话/团队任务板                        | M4         | ui-subagent / agent-team  |
| 定时任务 UI                              | ❌                  | 列表/创建/暂停（无 Temporal）            | M4         | ui-schedule               |
| 开发者控制台（API Key/MCP/Webhook/日志） | ❌                  | 自助接入                                 | M3         | —                         |
| 设置：模型/权限/Skills/MCP/搜索          | ❌ 仅组织/成员/会话 | 分区设置                                 | M1/M3      | ui-settings-\*            |
| 通知/未读                                | ❌                  | 站内通知优先；邮件后置                   | M4         | —                         |
| 主题/i18n                                | ❌ 固定暗色/中文    | 后置                                     | M4         | ui-theme                  |

---

## 3. 服务 / Agent 功能矩阵

| 能力                                      | 现状                                                        | 目标                                                                      | 里程碑               |
| ----------------------------------------- | ----------------------------------------------------------- | ------------------------------------------------------------------------- | -------------------- |
| Sandbox（隔离/执行/预览）                 | ⏸ 暂缓                                                     | 先决策替代（Fly Machines/自建 runner/只读）                               | M1 前                |
| GitHub 集成                               | ❌                                                          | GitHub App + 只读仓库/分支/PR                                             | M1                   |
| 文件系统工具（read/write/edit/glob/grep） | ⏸ 依赖 sandbox                                             | 先只读+补丁建议；执行后置                                                 | M1（条件）           |
| Shell / 终端                              | ⏸ 依赖 sandbox                                             | 决策后实施                                                                | M1（条件）           |
| Git 工具（status/diff/commit/branch/PR）  | ⏸ 依赖 sandbox                                             | 只读 diff 先做；写操作后置                                                | M1（条件）           |
| 工具注册表 + 权限策略                     | ⚠️ 7 个硬编码工具                                           | Tool Registry + auto/confirm/deny                                         | M1                   |
| Skills                                    | ❌                                                          | 注册表 + 首批 coding/office 技能                                          | M1/M2                |
| MCP Client                                | ❌                                                          | 租户配置、命名空间、审批                                                  | M1（GitHub 优先）/M3 |
| MCP Server / 开放 API                     | ❌                                                          | 对外工具/资源、OAuth、Webhook                                             | M3                   |
| 计划/Todo/Goal/反问                       | ⚠️ 仅 plan markdown                                         | 运行时工具 + UI                                                           | M1                   |
| 上下文压缩 / token 计量                   | ❌                                                          | 有界摘要 + token meter + 预算                                             | M1                   |
| 记忆（偏好/事实/摘要）                    | ❌                                                          | knowledge 记忆表 + 读写                                                   | M2                   |
| 邮件 OAuth（Gmail/M365）                  | ❌ 仅 IMAP 授权码                                           | OAuth 双轨                                                                | M2                   |
| 邮件线程/标签/已读/草稿/回复/转发         | ❌ 仅摘要                                                   | 完整邮件模型                                                              | M2                   |
| 邮件附件（下载/解析/预览）                | ❌                                                          | 受限存储 + PDF/docx/xlsx 解析                                             | M2（受限）           |
| 日历写操作                                | ❌ 仅读取                                                   | CRUD + 忙闲                                                               | M2                   |
| 文档生成/导出（docx/xlsx/pdf）            | ❌ 仅 markdown                                              | LibreOffice worker + DB/FS 存储                                           | M2                   |
| 知识库 rerank/引用/自动摄取               | ⚠️ 仅向量 top-k                                             | rerank + citation + 事件摄取                                              | M2                   |
| 队列 / 长任务                             | ❌                                                          | PG job 表 + worker（不引入 Temporal）                                     | M0                   |
| 用量/配额/BYOK                            | ❌ 仅 quota 字段                                            | usage_ledger + 配额执行 + BYOK                                            | M0/M3                |
| 订阅/支付                                 | ⏸ Stripe 暂缓                                              | Free 档 + 手动开通；自助支付后置                                          | M3 后置              |
| 观测                                      | ⚠️ 结构化日志                                               | Sentry 官方 + traceId；Langfuse 暂缓                                      | M0/M4                |
| 部署                                      | ⚠️ compose 仅 PG/Redis                                      | Dockerfile + Vercel + 云 PG/Redis（SaaS）；Linux VM + systemd（内网）     | M0（内网后续）       |
| 邮件实时推送                              | ❌                                                          | Pub/Sub / Graph webhook / IMAP IDLE                                       | M2                   |
| 内网/离线部署（无外网启动）               | ⚠️ 当前代码默认 SQLite/本地嵌入可离线跑，BFF/模型地址未打通 | Linux VM + systemd + Nginx + HTTP + SQLite + 内网模型 api_base + 断网验收 | **⏸ 暂缓（M0-13）** |

---

## 4. 里程碑与任务拆解

> 规模：S（≤2 天）/ M（3–5 天）/ L（1–2 周+）。⏸ = 依赖暂缓项决策。

### M0 平台基础 + 体验闭环（2–3 周）

| ID    | 任务                                                         | 端          | 验收标准                                          | 依赖     | 规模 |
| ----- | ------------------------------------------------------------ | ----------- | ------------------------------------------------- | -------- | ---- |
| M0-01 | Vercel 项目拆分：SPA + `/api` BFF；独立静态官网              | Web         | 两个 preview URL；SPA 路由回退；官网可抓取        | —        | M    |
| M0-02 | 六服务容器化 + Fly/Cloud Run + 健康检查 + 迁移 job           | Service     | staging 一键部署/回滚                             | —        | M    |
| M0-03 | 云 PG/云 Redis 接入；开发 SQLite/本地 Redis；CI 对 PG 跑迁移 | Service     | 生产 URL 注入即用；SQLite 开发零依赖；PG 迁移通过 | M0-02    | M    |
| M0-04 | PG job 表 + worker（SKIP LOCKED、lease、重试）               | Service     | 函数超时不杀 run；worker 崩溃可恢复               | M0-02    | M    |
| M0-05 | account-service 社交 OAuth（Google/GitHub）+ BFF Cookie 会话 | Web/Service | 登录/登出/续期；无 localStorage token；CSRF 防护  | —        | M    |
| M0-06 | BFF：代理、错误契约、限流（Redis/进程内降级）、下载          | Web         | 429 带重试头；错误统一；模块可测                  | M0-01/05 | M    |
| M0-07 | SSE `Last-Event-ID` + 自动重连 + 断点续传                    | Web         | 断网恢复不重复/不丢事件                           | —        | S    |
| M0-08 | Markdown/代码/表格渲染                                       | Web         | 流式安全渲染；XSS 测试通过                        | —        | S    |
| M0-09 | 会话管理：重命名/删除/归档/搜索 + 分页                       | Web/Service | 列表 cursor；搜索标题/内容；软删除                | —        | M    |
| M0-10 | 产物预览：Markdown/图片 + 下载；PDF/Office 标注后置          | Web         | 预览/下载可用；多实例用 database store            | M0-03    | M    |
| M0-11 | Sentry 接入 + 用量只读页 + 模型列表 + BYOK 占位              | Web/Service | 前端/服务错误可见；用量页有数据；模型来自 API     | M0-02    | M    |
| M0-12 | 附件最小版：DB/本地文件 + 类型/大小限制                      | Web/Service | 图片/小文档可上传/预览；超限提示                  | M0-03    | M    |

**M0 验收场景**：注册登录 → 新建会话（Markdown 流式）→ 上传小附件 → 重命名/搜索会话 → 产物预览/下载 → staging 回滚。

### M1 Coding（受限，4–6 周）

> 前置：**M1-00 沙箱替代方案拍板**（2026-10-20 已拍板：microsandbox；P1 沙箱底座已落地，见
> `docs/gap-2026-10/04-sandbox-selfhost.md` 实施记录）。P2/P3 接入用户 skill 包与 stdio MCP 前，
> 仍只交付「只读代码分析 + 补丁建议」，不承诺执行/终端/预览。

| ID    | 任务                                                               | 端             | 验收标准                           | 依赖  | 规模 |
| ----- | ------------------------------------------------------------------ | -------------- | ---------------------------------- | ----- | ---- |
| M1-00 | 沙箱替代决策与 spike：microsandbox / Fly Machines / 自建 gVisor runner | 架构           | 决策：microsandbox + provider 抽象 | —     | S    |
| M1-01 | Sandbox 实现（按 M1-00 结论）                                      | Service        | P1 底座已完成；执行接入 P2/P3      | M1-00 | L    |
| M1-02 | GitHub App + 只读仓库/分支/文件/PR 读取                            | Service        | 连接仓库；文件读取；不落前端令牌   | M0-05 | M    |
| M1-03 | Workspace 抽象（repo/mailbox/docs/kb）+ 文件 API                   | Service        | 按类型加载工具集；只读文件树       | M1-02 | M    |
| M1-04 | 只读代码分析 + 补丁建议（GitHub API + LLM）                        | Service        | 产出 patch/diff 与说明；不执行代码 | M1-02 | M    |
| M1-05 | FS 工具（read/write/edit/glob/grep）                               | ⏸ Service     | 依赖 M1-01；路径逃逸拦截           | M1-01 | M    |
| M1-06 | Shell/终端 + 后台任务                                              | ⏸ Service     | 依赖 M1-01；危险命令确认           | M1-01 | M    |
| M1-07 | Git 工具 + 快照回滚/PR                                             | ⏸ Service     | 依赖 M1-01/02                      | M1-01 | M    |
| M1-08 | 文件树 + 终端 + 后台任务 UI                                        | Web            | 只读文件树先做；终端随 M1-06       | M1-03 | M    |
| M1-09 | Diff review UI（逐文件/行评论/应用）                               | Web            | 大 diff 虚拟滚动；评论可提交       | M1-04 | M    |
| M1-10 | 预览 URL                                                           | ⏸ Service/Web | 依赖 M1-01；TTL/鉴权               | M1-01 | M    |
| M1-11 | 权限策略：per-tool/connector/tenant                                | Service/Web    | 设置页可调档；审计可见             | M1-04 | M    |
| M1-12 | Coding Skills：审查/问答/补丁建议（执行类后置）                    | Service        | 每个技能有 schema 与交付物         | M1-04 | M    |
| M1-13 | Plan/Todo/Goal/ask_user_question + 上下文压缩/token 预算           | Service/Web    | 计划可编辑；Todo 实时；超预算终止  | —     | L    |

### M2 Office + 体验（4–6 周）

| ID    | 任务                                                   | 端          | 验收标准                         | 依赖  | 规模 |
| ----- | ------------------------------------------------------ | ----------- | -------------------------------- | ----- | ---- |
| M2-01 | Gmail OAuth + token 加密存储                           | Service     | 最小 scope；凭证不外泄           | M0-05 | M    |
| M2-02 | Microsoft Graph OAuth（邮件/日历）                     | Service     | 同一连接器抽象                   | M2-01 | M    |
| M2-03 | 增量同步 + 实时推送（Pub/Sub/Graph webhook/IMAP IDLE） | Service     | 游标续传；新邮件触发事件         | M2-01 | L    |
| M2-04 | 邮件模型：线程/标签/已读/草稿/回复/转发                | Service/Web | 列表/详情/操作闭环               | M2-03 | L    |
| M2-05 | 附件受限存储 + 解析（PDF/docx/xlsx）                   | Service/Web | 大小上限；解析文本可入知识库     | M0-12 | M    |
| M2-06 | 日历写操作 + 忙闲 + 冲突提示                           | Service/Web | 创建/改期/取消；时区正确         | M2-02 | M    |
| M2-07 | 文档生成/导出（docx/xlsx/pdf）+ Office→PDF worker      | Service     | 模板渲染；转换隔离；DB/FS 下载   | M0-03 | L    |
| M2-08 | 知识库 rerank + 引用溯源 + 自动摄取 + 记忆             | Service/Web | 回答带 citation；偏好/事实可读写 | M0-09 | L    |
| M2-09 | Office Skills：摘要/回复草稿/分类/周报/附件归档        | Service     | 技能模板 + 交付物                | M2-04 | M    |
| M2-10 | 模板画廊 + 场景选择                                    | Web         | 首启引导；模板可预览再运行       | M1-12 | M    |
| M2-11 | 发信/删除审批 + 审计导出                               | Service/Web | 敏感动作默认 confirm             | M1-11 | M    |
| M2-12 | 工具级用量计量                                         | Service     | usage_ledger 有明细；页面可查    | M0-11 | M    |

### M3 开放平台 / MCP（4–6 周）

| ID    | 任务                                      | 端          | 验收标准                                   | 依赖     | 规模 |
| ----- | ----------------------------------------- | ----------- | ------------------------------------------ | -------- | ---- |
| M3-01 | API Key：创建/轮换/吊销/scope/哈希        | Service     | 只显示一次明文；权限最小化                 | M0-05    | M    |
| M3-02 | Public REST `/v1`                         | Service     | OpenAPI；幂等键；cursor；错误契约          | M3-01    | L    |
| M3-03 | Public SSE + Webhook（签名/重试）         | Service     | 断线续传；死信可见                         | M3-02    | M    |
| M3-04 | MCP Server（streamable HTTP + OAuth 2.1） | Service     | 外部可列/调工具与资源                      | M3-01    | L    |
| M3-05 | MCP Client 注册表 + 租户配置 + 审批       | Service/Web | 可接 GitHub/Notion/飞书                    | M1-11    | L    |
| M3-06 | 开发者控制台                              | Web         | 自助接入；日志可检索                       | M3-02    | M    |
| M3-07 | Skill 注册表（manifest/版本/发布/灰度）   | Service/Web | 版本化；回滚                               | M1-12    | M    |
| M3-08 | 用量/配额/BYOK（Stripe 暂缓）             | Service/Web | Free 额度生效；BYOK 不扣 credits；超限 402 | M0-11    | M    |
| M3-09 | 限流/防滥用（Redis/进程内降级）           | Service/Web | 租户/IP/Key 三级                           | M3-01    | M    |
| M3-10 | 文档站/SDK（TS/Python）/示例              | Web/Site    | 5 分钟跑通 quickstart                      | M3-02/04 | M    |
| M3-11 | 订阅/支付（Stripe 后置）                  | Service/Web | 自助升级/发票                              | M3-08    | ⏸   |

### M4 规模化

| ID    | 任务                         | 说明                                      |
| ----- | ---------------------------- | ----------------------------------------- |
| M4-01 | 子代理 / Agent 团队 / 任务板 | 并行、消息、依赖、写域                    |
| M4-02 | 调度/通知                    | 站内通知优先；邮件/SMTP 后置              |
| M4-03 | Sentry 深化 + Evals          | 无 Langfuse 阶段用自建事件表 + golden set |
| M4-04 | 灰度/回滚/容量               | Fly + Vercel 双端演练                     |
| M4-05 | i18n / 移动 PWA / 分享页     | 后置                                      |
| M4-06 | 企业：SSO/RBAC/审计导出      | 后置                                      |
| M4-07 | MCP/Skill 市场               | 提交/审核/安装                            |
| M4-08 | 对象存储 / 多区域 / 数据驻留 | 视合规与规模                              |

---

## 5. 当前代码可立刻做的 Quick Wins

| #   | 事项                                  | 位置                                                                | 收益                            |
| --- | ------------------------------------- | ------------------------------------------------------------------- | ------------------------------- |
| 1   | SSE 重连带 `Last-Event-ID` + 自动重连 | `apps/web/src/lib/runs-api.ts`、`components/runs/use-run-events.ts` | 断线不重复/不丢                 |
| 2   | Markdown 渲染                         | `pages/chat.tsx`、`run-detail.tsx`、`changes.tsx`                   | 交付可读性                      |
| 3   | 会话重命名/删除/搜索/分页             | `api/v1/conversations.py` + 侧边栏                                  | 真实使用必需                    |
| 4   | 产物预览/下载（DB/文件版）            | `pages/changes.tsx`、`chat.tsx`                                     | 交付体验                        |
| 5   | 模型 chip 接真实数据                  | `components/console/topbar.tsx`                                     | 去掉假下拉                      |
| 6   | 审批策略抽配置                        | `services/agent-orchestrator/services/runs.py`                      | 为 coding 安全铺路              |
| 7   | PG job 表 + worker 骨架               | 新增表 + worker                                                     | 替代 Temporal 的关键            |
| 8   | 服务 Dockerfile + 本地 compose        | `deploy/`                                                           | 可本地/本机运行（内网部署后续） |
| 9   | Sentry 接入                           | web + services                                                      | 线上可观测                      |

---

## 6. 非目标 / 不照搬

- 🚫 本期不做：E2B 沙箱、Temporal/Inngest、Logto、Stripe、Resend、Langfuse、对象存储、多区域；
- 🚫 完整 IDE / LSP / 浏览器 / Computer Use（coding 执行能力先解决隔离问题）；
- 🚫 本地优先插件热更新 / Cordis / Desktop；
- 🚫 首期开放平台做计费分成/插件市场。

---

## 7. 风险与验证

| 风险                     | 验证/缓解                                                                            |
| ------------------------ | ------------------------------------------------------------------------------------ |
| 无沙箱 → coding 只能只读 | M1-00 前不对外承诺「可执行」；只读方案先上线                                         |
| PG job 队列吞吐/公平性   | 压测 100 并发 runs；lease/重试/死信；必要时换专业队列                                |
| SQLite 与 PG 行为差异    | CI 对云 PG 跑迁移 + 集成；SQLite 只开发                                              |
| 多实例 + filesystem 产物 | 生产强制 database 模式；对象存储后置时限制文件大小                                   |
| Redis 单点               | 只用于限流/锁/缓存；降级进程内；关键状态落 PG                                        |
| 自建认证安全             | 复用旋转刷新令牌/JWKS；社交 OAuth 做安全评审                                         |
| Vercel SSE 限制          | 长流直连 runtime；BFF 只代理短流                                                     |
| BYOK 无 KMS              | 应用层加密 + 轮换 + 不落日志；KMS 后置                                               |
| 无 Stripe 收入           | Free/邀请制先跑；付费档后置，避免过早计费复杂度                                      |
| 本地启动失败             | 检查 .env / AUTO_CREATE_TABLES / 端口 / 依赖同步；六服务 healthz + web 首页 200 基线 |
