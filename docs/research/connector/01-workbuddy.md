# WorkBuddy 连接器调研

> 调研时间：2026-11-03 ｜ 调研人：workbuddy-researcher
> 结论分两档：**事实**（带来源 URL / 仓库路径）与**推断**（无直接证据，标注依据）。未能联网核实项集中列在文末。
> 对 beauty-moon 的落地建议见 §8；与 `docs/architecture.md` 现有规划的对照见 §8.2。

## 0. 结论速览（TL;DR）

1. **WorkBuddy 是腾讯云 CodeBuddy 团队的 AI Agent 桌面工作台**（Win/macOS 客户端 + 云端沙箱双模式，Craft/Plan/Ask 三模式），不是 dsh 生态产品，与字节"豆包工作"无关（推断：仅同为桌面 Agent 竞品）。来源：<https://www.workbuddy.cn/llms.txt>、<https://www.codebuddy.cn/docs/workbuddy/Overview>
2. **连接器 = 第三方服务接入协议 + 能力描述（Skill）+ 授权模式三件套**，不是简单的"API Key 配置项"。官方把连接器定位为"扩展维度"之一，与专家/技能/资料库并列。
3. **官方定义两种接入形态：MCP + Skill（推荐 ⭐⭐⭐）与 CLI + Skill（⭐⭐）**，一个 Connector 只能二选一；MCP 零安装、CLI 可托管 Node/Python 运行时并隔离依赖。
4. **授权模式只有三档**：无认证 / `server-side`（云端 OAuth，**端侧不持有第三方 token**）/ `token`（用户表单自填，凭证存本机 `~/.workbuddy`）。一个 Connector 只能选一档。
5. **连接器市场体量大**：第三方归档显示 **531 个连接器**、295 个技能包、429 个专家包（同步于 2026-11-02）；官方消费版文档只点名 6 个内置连接器（QQ 邮箱、腾讯文档、腾讯乐享、腾讯会议、TAPD、腾讯网盘）。来源：<https://github.com/infometa/workbuddyskills>（第三方归档，非官方）
6. **数据同步是"按需拉取"而非"后台同步"**：官方权限三原则明写"不主动定时抓取"；连接器读写不扣积分，AI 理解/汇总外部数据才扣积分。
7. **对 agent 的暴露方式是"工具 + 说明"**：MCP 提供工具清单（可按 `disabledTools` 黑名单裁剪），Skill 用 Markdown 逐工具写用途/参数/示例/错误处理；调用入口是 `+` 菜单与"按需自动弹出引导卡片"，**未见 `@` 引用语法**。
8. **最值得抄的三件事**：云端 OAuth 托管 + 端侧零 token；授权失效的"单步自动重试一次"中断恢复 UX；连接器 manifest 的版本门槛与降级策略（`minWorkbuddyVersion` 未启用不展示、已连接置灰提示）。

## 1. WorkBuddy 是什么

### 1.1 身份与版本（事实）

| 项       | 内容                                                                                     | 来源                                                     |
| -------- | ---------------------------------------------------------------------------------------- | -------------------------------------------------------- |
| 归属     | 腾讯云 CodeBuddy 团队                                                                    | <https://www.workbuddy.cn/llms.txt>                      |
| 形态     | 桌面 AI Agent 工作台（本地客户端 Win/macOS + 云端沙箱）                                  | 同上；<https://www.codebuddy.cn/docs/workbuddy/Overview> |
| 定位     | "腾讯出品的全场景 AI 工作台"：自然语言 → 自主规划执行 → 交付文档/表格/PPT                | <https://www.codebuddy.cn/docs/workbuddy/Overview>       |
| 公测     | 2026-03-09 正式公测，中文俗称"小龙虾"                                                    | llms.txt / 官方站点                                      |
| 工作模式 | Craft / Plan / Ask 三模式；多任务并行、右侧结果区、本地文件操作                          | llms.txt                                                 |
| 模型     | 多模型（Kimi / MiniMax / GLM / DeepSeek / Hunyuan），支持自定义 API Key                  | llms.txt                                                 |
| 扩展体系 | 专家 / 技能 / 连接器 / 资料库                                                            | Overview                                                 |
| 生态兼容 | 兼容 OpenClaw Skills（自述 5000+ 技能包）；MCP 可挂第三方 Server；支持 cron 风格定时任务 | llms.txt                                                 |
| 企业版   | 腾讯云 WorkBuddy Enterprise，独立文档与计费                                              | <https://cloud.tencent.cn/document/product/1831>         |

### 1.2 与相近产品的区分

- **vs dsh desktop**：dsh 是本地 harness/工作台；WorkBuddy 是腾讯闭源商业产品（客户端 + 云服务），两者无代码关系（推断，依据：官方文档与 llms.txt 未提及任何 dsh 关联）。
- **vs 豆包工作 / 字节系**：无股权与生态关系（推断，仅同为"桌面 Agent"赛道竞品）。
- **vs CodeBuddy（编程助手）**：同底座、两条产品线——CodeBuddy 面向编码，WorkBuddy 面向办公（事实，llms.txt 明示团队归属与产品分工）。

## 2. 支持哪些连接器

### 2.1 官方内置清单（事实）

**消费版**（<https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Connector>）：

- QQ 邮箱、腾讯文档、腾讯乐享、腾讯会议、TAPD、腾讯网盘
- 另有「自定义连接器」：用户自行填写 MCP 服务地址与鉴权方式，"访问范围由您的配置决定，WorkBuddy 不预设范围"（= 自定义 MCP Server 入口）

**企业版**（<https://intl.cloud.tencent.com/zh/document/product/1316/84494>，更新 2026-09-30）：

- QQ 邮箱、腾讯乐享、腾讯文档、TAPD、**微云**，及自定义连接器
- 注意与企业版的清单差异：企业版写"微云"，消费版写"腾讯网盘/腾讯会议"——**同一能力在不同版本文档里命名不一致**，接入时以客户端实际卡片为准。

### 2.2 市场规模（事实，第三方归档）

<https://github.com/infometa/workbuddyskills> 的 `CATALOG.md`（同步于 2026-11-02）统计：

| 分类                              | 数量    |
| --------------------------------- | ------- |
| 连接器 `connectors/`              | **531** |
| 技能包 `skills/`                  | 295     |
| 专家包 `experts/`                 | 429     |
| 官方插件 market / 团队插件 market | 60 / 30 |

- 归档 README 明示内容来自"WorkBuddy / CodeBuddy 公开市场包"，仅作学习归档，**版权归腾讯相关产品**；连接器条目仍需在 App 内完成 OAuth/扫码才能真正启用。
- 531 个连接器里绝大多数是第三方长尾 SaaS（表格/HR/投研/医疗/营销/会议等），例如 `connectors/github`、`connectors/notion`、`connectors/dingtalk`、`connectors/awesun`（向日葵远程控制 CLI）。
- 目录内也混有以 MCP 命名的条目（如 `alphapai-lite-mcp`、`amazingdata-mcp`、`archive-hospital-mcp`），说明 MCP 是主流接入方式。

### 2.3 飞书 / Notion 的具体情况（事实 + 边界）

- **官方连接器清单中没有"飞书"连接器**（消费版/企业版文档均未列出）。
- 飞书存在三条路径：
  1. **IM 集成**：「助理」的"飞书集成"通道，填 App ID + App Secret，走 WebSocket 长连接、无需公网回调；可在飞书内远程指挥本机 WorkBuddy 并回传结果。来源：<https://www.feishu.cn/content/article/7651905073454304222>（飞书官网博客，2026-06-16）
  2. **CLI 方案**：开发者文档把 `lark-cli` 作为多步认证示例（`config init --new` → `auth login`，`authWaitForExit: true`）。来源：<https://open.workbuddy.cn/docs/connector>
  3. **市场第三方**：归档中 `ezjoin-meeting` 等条目提到"需要飞书应用凭证或用户登录授权"。
- **Notion**：官方文档未列入内置清单，但市场归档有 `connectors/notion`；开发者文档同时把 `notion CLI` 列为 CLI 接入示例（`authSuppressBrowser`）。→ 结论：Notion 以"市场/自定义连接器"形式存在，非内置。

## 3. 授权绑定流程与 UX

### 3.1 三种触达路径（事实，消费版文档）

1. **卡片直连**：左侧边栏「专家·技能·连接器」→「连接器」页签 → 卡片右上「+」→ 登录/授权 → 卡片出现绿色圆点 +「连接器 XX 已连接」+「去对话」。
2. **任务前选择**：新建任务输入框左下 `+` → 连接器 → 勾选启用（未授权先授权）。
3. **任务中按需连接**：执行到需要未连接连接器时，输入框上方**自动弹出连接引导卡片**；授权完成后**自动继续当前任务，无需重发指令**。

路径 3 是最关键的差异化设计：**授权是任务执行的中断点，而不是前置条件**。

### 3.2 授权失效的重连 UX（事实）

- 出现「连接器需要重新连接」卡片 → 「重新连接」→ 登录/扫码/授权 → 成功后**自动重试刚才失败的那一步**。
- 同一步骤**只自动重试一次**；可关闭卡片（放弃重试）。
- 触发条件严格：**仅当连接器明确返回"授权失效"**才弹；网络超时 / 服务异常 / 无权限都不弹。→ 这是把"凭证错误"与"业务错误"在协议层分开，避免误打扰。

### 3.3 关闭 vs 解绑（事实）

| 操作     | 语义                         | 位置                         |
| -------- | ---------------------------- | ---------------------------- |
| 关闭启用 | 保留授权与凭证，任务中不参与 | `+` > 连接器 的开关          |
| 解绑     | 清除连接，卡片恢复"未连接"   | 连接器管理页卡片弹窗「解绑」 |

### 3.4 自然语言管理（事实）

- 安装："帮我安装 QQ 邮箱连接器"
- 查看："我安装了哪些连接器"（会区分"已授权但当前关闭"）
- 卸载：确认弹窗列出清单与数量；**内置连接器不支持卸载**。

### 3.5 授权页面实证（事实）

- **QQ 邮箱**：扫码（QQ 邮箱 App 7.1.5 / 鸿蒙 0.2.9+）→ 手机确认权限清单（账号信息 / 读取近一个月收件箱 / 代发邮件 / 可选删除邮件）→ 授权成功 → 浏览器弹窗点「打开」完成。撤销在 QQ 邮箱 App「设置-账号-安全管理-登录设备和授权码-应用授权」。
- **腾讯乐享**：走乐享知识库 MCP，按用户已有权限访问；跳转乐享登录（微信/手机号）→ 展示将获得的权限 → 「继续」。
- **企微打通后的授权弹窗**（媒体实测）：任务触发时弹出授权窗口，**列明哪些信息可读、哪些操作需授权**再确认。来源：<https://www.163.com/dy/article/L9CM66V20511805E.html>（人人都是产品经理，2026-10-16，报道 2026-10-14 WorkBuddy 与企业微信深度打通）

## 4. 连接管理页面

事实（消费版 + 企业版文档）：

- 入口：左侧导航「连接器」/侧栏「专家·技能·连接器」→「连接器」页签。
- 页面主体是**连接器卡片网格**：未连接显示「+」，已连接显示绿色圆点与名称，卡片可「去对话」。
- 卡片操作：启用/禁用开关（已连接卡片右侧）、解绑（弹窗确认）、卸载（仅市场连接器）。
- 右上角「自定义连接器」：填 MCP 服务地址 + 鉴权方式即可挂自有 Server（企业版同样支持）。
- 消息输入框内提供快捷管理（企业版文档明示"消息框内可快捷管理"）。

推断：页面本身没有"每连接器权限范围编辑器"——权限范围在**第三方授权页**决定（如 QQ 邮箱的四个 scope 就是在 QQ 侧勾选的），WorkBuddy 只做展示与开关。依据：两端文档均只描述开关/解绑，未出现 scope 编辑 UI。

## 5. 凭证与权限模型

### 5.1 三档认证（事实，开发者文档 <https://open.workbuddy.cn/docs/connector>）

| auth_mode            | 凭证归属                                                   | 用户体验              | 适用                              |
| -------------------- | ---------------------------------------------------------- | --------------------- | --------------------------------- |
| 省略                 | 无                                                         | 点「连接」即用        | 公开数据源                        |
| `server-side` ⭐推荐 | **WorkBuddy 云端托管，端侧不持有第三方 token**，请求时注入 | 首次/失效开浏览器授权 | 有标准 OAuth 的服务               |
| `token`（v4.23.0+）  | **本机 `~/.workbuddy`**，由 `token-schema.json` 表单收集   | 填表，不开浏览器      | 只有长期 API Key 的服务、私有部署 |

关键约束：

- 一个 Connector 只有一个 `auth_mode`；要同时支持 OAuth 与 Token 必须提交**两个 source**。
- `server-side` 有两种注册方式：`mcp_oauth`（云端动态注册 DCR，RFC 7591 + RFC 9728 元数据发现）与 `oauth2_code`（预注册，提供 client_id/secret + 授权/Token 端点 + `provider_config`，含 PKCE、token 端点认证方式、`token_response_map` 字段映射、refresh 配置）。
- 授权服务端需要给 `client_name`（CodeBuddy 或 WorkBuddy）+ 5 条域名（支持通配符多级子域）加白；未认证请求必须返回 401 且 `WWW-Authenticate` 携带 `resource_metadata`；端点强制 HTTPS；`redirect_url` 由系统按 `source` 生成，开发者不可填。
- `token` 模式的注入位置随传输类型而变：stdio → `env`；sse/streamableHttp → `headers` 或 URL；变量用 `${VAR}` 引用。
- refresh 过期无法自动续期 → 提示重新授权；建议 refresh_token 有效期 ≥ 30 天；云端 OAuth 的刷新由 WorkBuddy 服务端完成。
- 敏感字段必须声明为 `password` 类型；日志/错误不得输出完整凭证；不得硬编码真实 Token。

### 5.2 CLI 方案的凭证归属（事实）

- WorkBuddy 只调度 `init → auth → status → unAuth` 四个命令，**不规定、不迁移、不清理** CLI 自己的凭证文件；凭证由 CLI 自行持久化。
- 因此：不得把凭证写进 npm/pip 安装目录；不得在 `env` 里硬编码客户端目录路径（用 `$HOME` 占位）。
- **重启后只跑 `status` 不跑 `auth`** → CLI 的 token 必须跨进程重启持久，否则每次都要重新授权。

### 5.3 权限边界（事实，消费版文档"信息收集表"）

| 数据类别                                          | 是否必要 | 说明             |
| ------------------------------------------------- | -------- | ---------------- |
| 第三方账号标识                                    | 必要     | 建立连接         |
| 授权凭据（OAuth Access/Refresh Token 或 API Key） | 必要     | 用于代调用       |
| 第三方业务数据                                    | 按需     | 仅指令触发时读取 |
| 用户写入数据                                      | 非必要   | 需明确指令确认   |

三条原则（原文）："每个连接器独立授权""不主动定时抓取""不超出您已有权限范围"。

补充事实：连接器读写**不消耗积分**，WorkBuddy 理解/汇总外部数据**消耗积分**——即计费落在模型侧而非连接侧。

## 6. 数据同步方式

事实：

- **没有后台同步引擎**：官方明示"不主动定时抓取"，连接器是**调用时实时访问**（MCP 请求 / CLI 执行），不做定时拉取入库。
- MCP 请求带 `timeout`（默认 30000ms）、可按 `disabledTools` 黑名单裁剪工具，属于"请求-响应"模型。
- CLI 方案同理：每次任务执行时调 CLI 命令，读第三方 API。
- 资料库（知识库）是独立维度，与连接器解耦——连接器不负责把第三方数据灌进本地库。

推断：这解释了为什么官方在权限表里把"第三方业务数据"标为"按需读取"——架构上是 on-demand 拉取，不做持久镜像，因此也不需要增量同步/冲突解决。

## 7. 连接器能力如何暴露给 agent

事实（开发者文档）：

1. **工具层 = MCP 工具清单**。`mcp.json` 里 `mcpServers` **只能有一个 Server**（系统取第一个），传输支持 sse / streamableHttp / stdio。
2. **说明层 = Skill（`skills/{name}/SKILL.md`）**：Markdown + YAML frontmatter（name/description/version/author），逐工具/逐命令写"用途 + 参数表 + 示例 + 错误与认证说明"。MCP 方案可选（MCP 自带工具描述），**CLI 方案强烈推荐**（CLI 没有标准工具描述）。
3. **工具裁剪**：`disabledTools` 黑名单（v4.22.15+）可在 manifest 里关掉不希望暴露给模型的工具。
4. **调用入口**：`+` 菜单勾选、连接器卡片"去对话"、执行中自动引导卡片。**官方连接器文档中未见 `@` 引用语法**（据 <https://open.workbuddy.cn/docs/connector> 与消费版连接器文档，均以"选择/自动引导"描述）。
5. **CLI 与 MCP 不可混用**；一个 Connector 只能一个 MCP Server；`preAuth: "cli"`（v5.0.0）允许先跑 CLI 认证再连 MCP（把 CLI 当凭证获取器）。
6. **版本闸门**：`minWorkbuddyVersion` / `maxWorkbuddyVersion`——低于下限的客户端，未启用过的连接器**不展示**；已连接的**置灰降级提示升级**；上限用于紧急下架。

## 8. 对 beauty-moon 的启示

> 现状对照：`services/connector-service/README.md`（租户隔离连接存储 + Fernet 信封加密，仅列表/详情/撤销，无创建入口、无 OAuth、无同步）；`docs/architecture.md:270` 规划的 MCP Client（GitHub/Notion/飞书/Slack）；`docs/architecture.md:434` 的 per-tenant/per-connector/per-tool `auto|confirm|deny` 审批策略。

### 8.1 可直接借鉴的设计

1. **认证分档而非一套 OAuth 走天下**：WorkBuddy 的三档（无认证 / 云端 OAuth 托管 / 用户自填 token）覆盖了公开源、标准 SaaS、私有部署三类场景。我方 `credential_secret` + Fernet 加密天然对应 `token` 档；`server-side` 档需要新增"授权会话 + refresh 托管 + 代注入"三件套，工作量最大。
2. **端侧零 token 是安全卖点**：`server-side` 的 token 只在云端，MCP 请求由云端注入。对应到我方：**连接器凭证不应下发给 agent-orchestrator / tool-service 进程**，由 connector-service 代理注入更安全（可与我方"凭证只进不出"的既有约束对齐）。
3. **授权中断 + 单步重试**：授权在任务中途发生，授权成功后自动续跑且**只重试一次**。映射到我方 run 编排：run 进入 `awaiting_connector_auth` 状态 → 前端弹卡片 → 授权回调 → 从失败步恢复；失败步只恢复一次，避免死循环。
4. **错误语义分层**：只有"授权失效"才触发重连 UI，超时/5xx/无权限不触发。建议我方 connector-service 定义专属错误码（如 `connector.auth_expired` 与 `connector.upstream_timeout` 分开），否则前端会把所有 4xx/5xx 都当"要重连"。
5. **连接器 manifest 与版本闸门**：单文件 manifest（`connector-meta.json`）+ 版本下限 + `disabledTools` 黑名单，是"让第三方/内部团队自助接入"的最低成本结构。若我方要开放第三方连接器，这套字段可直接参考（source 全局唯一 kebab-case、`name_map` 多环境文案覆盖等我方暂不需要）。
6. **不做后台同步**：官方明确不做定时抓取，直接省掉同步状态机、去重、增量游标等一整块复杂度。我方 `docs/architecture.md` 也未规划同步，建议**明确写成"on-demand 拉取"**，避免后续被要求"定时同步飞书文档"。
7. **CLI 作为第二形态**：当第三方没有 MCP/HTTP API 时，CLI 方案（自动安装 + 四段生命周期 + 托管 Node/Python 运行时）是兜底。对我方成本过高，**建议先只做 MCP/HTTP**，把 CLI 列为暂不支持。

### 8.2 与我方既定规划的对齐/差异

| 维度     | WorkBuddy                                        | beauty-moon 现状                                          | 建议                                              |
| -------- | ------------------------------------------------ | --------------------------------------------------------- | ------------------------------------------------- |
| 连接存储 | 云端连接 + 市场 manifest                         | 已有 `connections` 表 + 租户隔离                          | 复用，补 `provider`/`auth_mode`/`scopes` 字段     |
| 凭证     | 云端托管（server-side）/ 本机（token）/ CLI 自持 | Fernet 信封加密，凭证只进不出                             | 保持；server-side 档需增加 refresh 托管与注入接口 |
| 授权入口 | `+` 菜单 / 卡片 / 执行中自动引导                 | 前端 `/connectors` 页面（当前占位或已下线）               | 至少要有"卡片 + 执行中引导"两处                   |
| 工具暴露 | 一个 Connector 一个 MCP Server + Skill 说明      | `docs/architecture.md:270` 规划 MCP Client + 工具命名空间 | 与规划一致；补 `disabledTools` 式黑名单           |
| 审批     | 授权粒度在第三方侧；WorkBuddy 无 per-tool 策略   | `:434` 已有 per-tool `auto/confirm/deny`                  | 我方更强，保留                                    |
| 同步     | 无（按需拉取）                                   | 未规划                                                    | 明确不做后台同步                                  |

## 9. 参考来源清单

| 类型       | 来源                                                                                                   | 说明                                                                   |
| ---------- | ------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------- |
| 官方       | <https://www.workbuddy.cn/llms.txt>                                                                    | 产品自述、能力矩阵（营销口径）                                         |
| 官方       | <https://www.codebuddy.cn/docs/workbuddy/Overview>                                                     | 产品定位                                                               |
| 官方       | <https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Connector> | 消费版连接器：清单、UX、授权实证、权限表、计费                         |
| 官方       | <https://open.workbuddy.cn/docs/connector>                                                             | **开发者规范全文**（MCP/CLI、三档 auth、manifest、发布审核、版本兼容） |
| 官方       | <https://intl.cloud.tencent.com/zh/document/product/1316/84494>                                        | 企业版连接器文档                                                       |
| 第三方归档 | <https://github.com/infometa/workbuddyskills>                                                          | 市场包离线归档：531 连接器 / 295 技能 / 429 专家；含 `CATALOG.md`      |
| 媒体实测   | <https://www.163.com/dy/article/L9CM66V20511805E.html>                                                 | 企微打通实测（2026-10-16），授权弹窗列明可读范围                       |
| 生态       | <https://www.feishu.cn/content/article/7651905073454304222>                                            | 飞书集成通道（WebSocket 长连接）                                       |

## 10. 未能核实 / 待补

1. **企业版是否有管理员统一下发/托管连接器授权**：腾讯云中文文档站（`cloud.tencent.cn/document/product/1831/*`）有 JS 反爬，只拿到 intl 镜像的连接器单页；管理后台能力**未能联网核实**。
2. **官方是否开源**：目前只见第三方归档（`infometa/workbuddyskills`，明确声明版权归腾讯）与社区项目（如 `codex-workbuddy-gateway`），**推断官方闭源**。
3. **531 个连接器的 auth_mode / MCP-CLI 分布**：GitHub API 被限流（403 rate limit），整仓 tarball 下载超时，未能统计；仅能从命名与条目抽样判断"MCP 为主"。
4. **`@` 引用机制**：官方连接器文档检索未见 `@` 语法，但未穷举全部 WorkBuddy 文档（技能/资料库章节可能另有引用语法），**推断为"无 @"**。
5. **飞书官方连接器**：消费版/企业版清单均无飞书；市场归档与 CLI 示例中存在飞书相关条目，是否为官方出品**未能核实**（存档无 publisher 字段）。
