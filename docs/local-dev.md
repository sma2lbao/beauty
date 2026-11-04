# 本地开发

面向「本地优先」开发:不需要 Docker、云 PG/Redis、Vercel 或真实 OAuth 凭据。
本地默认使用 SQLite + 文件系统产物 + LLM 桩。

## 1. 前置

- Node.js >= 22.22.2（jsdom 30 要求；本机 24.x）
- Python 3.12
- uv >= 0.5
- 首次使用先安装 JS 依赖:

```sh
npm install
```

各 Python 服务的 .env 由启动脚本自动从 .env.example 复制;需要自定义时再手动编辑
(services/\*/.env 已被 gitignore)。

## 2. 一键启动

```sh
npm run dev            # 七个业务服务 + API 网关 + Vite SPA
npm run dev:site       # 额外启动 Next.js 官网(:3000)
npm run dev:status     # 只检查健康状态
npm run dev:migrate    # 只把本地 SQLite 迁移到 alembic head(dev 启动前会自动执行)
```

脚本行为:

- 启动顺序并行;已有端口被占用则跳过并提示(不会杀已有进程);
- 缺 .env 自动创建;
- 启动前把 6 个带 alembic 迁移的服务的 SQLite 库推到 head(见 §7.1),失败则中止启动;
- 服务日志在 .cache/dev/\*.log;
- Ctrl+C 停止由脚本启动的全部进程;
- 任一子进程退出时整体退出,方便发现崩溃。

## 3. 端口

| 服务               | 端口 | 健康检查    |
| ------------------ | ---- | ----------- |
| account-service    | 8001 | /healthz    |
| agent-orchestrator | 8002 | /healthz    |
| llm-gateway        | 8003 | /healthz    |
| connector-service  | 8004 | /healthz    |
| workspace-service  | 8005 | /healthz    |
| knowledge-service  | 8006 | /healthz    |
| api-gateway        | 8007 | /api/health |
| tool-service       | 8008 | /healthz    |
| web                | 5173 | /           |
| site(可选)         | 3000 | /           |

connector-service(:8004)是连接器平台(内置 Notion 连接器:4 只读 + 2 写工具,写操作经对话确认;支持 OAuth 授权或粘贴内部集成令牌两种连接方式),Web 端有 `/connectors` 页面;邮件与日历能力已下线。

打开 http://localhost:5173。

**API 网关(:8007)是必需的**:Vite 的 /api 已统一代理到 API 网关,API 网关再转发
orchestrator/account/workspace/knowledge。忘记启动 API 网关时前端会表现为 401/502。

## 4. 鉴权模式

本地默认 VITE_AUTH_MODE=token:

- 登录走 account-service 邮箱+密码;
- 业务请求走 /api → API 网关(Bearer 直通兜底);
- 不依赖 Google/GitHub 凭据。

需要验证 Cookie 会话时,把 apps/web/.env.local 设为:

```env
VITE_AUTH_MODE=session
```

然后重启 Vite。session 模式下 API 网关会写加密 httpOnly Cookie,SPA 不接触 token。
OAuth 按钮只有在 account-service 配了 client id/secret 时才可用;本地不配也可用
邮箱+密码登录。

## 3.1 Skills（场景模板）

- 内置 manifest 在仓库根 `skills/*.json`（id/version/title/workspace_type/tools/system_prompt 等；`tools` 可缺省）。
- orchestrator 启动时加载，暴露 `GET /v1/skills`、`GET /v1/skills/{id}`。
- `POST /v1/runs` 或发消息时可带 `skill_id`：创建 run 时冻结 skill 快照（系统提示词 + 工具白名单），后续改 manifest 不影响已建 run。
- 工具白名单语义：**未声明 `tools`（接口返回 `null`）= 不限制**，装配全部工具；显式 `"tools": []` 才是"不用工具"。执行时按声明的白名单裁剪，例如知识库类技能只能 `search_knowledge`、`list_documents`/`read_document`，只读代码审查技能只能 `read`/`glob`/`grep`/`list_dir`（旧 `code_*` 名仍可用一个发布周期），不能写。
- Web `/skills` 可浏览技能；任务台输入区的「技能」选择器也可直接绑定。

## 3.2 连接器（内置 Notion：4 只读 + 2 写）

- **定位**：connector-service(:8004) 是连接器平台（OAuth 授权会话、凭证托管、连接生命周期：刷新单飞 + 24h 巡检 + outbox 生命周期事件）；内置 **Notion 连接器**（provider key `notion`、工具前缀 `Notion`；4 个只读工具 + 2 个写工具，写操作经对话内确认）。连接支持两种方式：**OAuth public integration** 或**直接粘贴 internal integration token**。邮件/日历能力已于 2026-10 下线，内置飞书 provider 已于 2026-11-04 按新规划移除。设计见 [design/connector.md](./design/connector.md)。
- **页面**：Web `/connectors` 列出 provider 与连接状态，支持授权、启用/停用、检查、刷新、解除连接。
- **接口**：`GET /v1/connectors`（目录）、`POST /v1/connectors/{provider}/authorize`、`GET /v1/oauth/{provider}/callback`、`GET /v1/connections`、`POST /v1/connections`（粘贴静态令牌建连接）、`GET/PATCH/DELETE /v1/connections/{id}`、`POST /v1/connections/{id}/refresh|check`；内部面 `GET /v1/internal/connectors/tools`、`POST /v1/connectors/{provider}/execute`（`X-Internal-Secret` + `X-Tenant-Id`）。
- **本地启用**：
  1. `cp services/connector-service/.env.example services/connector-service/.env`，按下列示例填值（`CONNECTOR__` 前缀）：
     ```dotenv
     CONNECTOR__PROVIDERS_ENABLED=notion
     CONNECTOR__NOTION_CLIENT_ID=<public integration 的 client id>
     CONNECTOR__NOTION_CLIENT_SECRET=<client secret>
     CONNECTOR__OAUTH_REDIRECT_BASE=http://localhost:5173/api/connector
     CONNECTOR__INTERNAL_SECRET=<内部密钥>
     CONNECTOR__CREDENTIAL_SECRET=<凭证信封密钥>
     ```
     直连 :8004 调试时把 `CONNECTOR__OAUTH_REDIRECT_BASE` 改为 `http://127.0.0.1:8004`。
  2. 在 Notion 建 **public integration**：Redirect URI 填 `{CONNECTOR__OAUTH_REDIRECT_BASE}/v1/oauth/notion/callback`（经网关为 `http://localhost:5173/api/connector/v1/oauth/notion/callback`，直连为 `http://127.0.0.1:8004/v1/oauth/notion/callback`）。回调是浏览器被授权页重定向回来的（没有会话凭证），所以 API 网关把它做成**公开路由** `/api/connector/v1/oauth/{provider}/callback`，原样转发给 connector-service 并透传它的 302 目标，不经过通用 `/api/{path}` 代理的鉴权；Capabilities 勾选 **Read content**（只读工具必需）与 **Insert content**、**Update content**（`Notion__create_page` / `Notion__append_blocks` 需要，改动后需重新授权）。
  3. 生成一个内部密钥同时写进 connector-service 的 `CONNECTOR__INTERNAL_SECRET` 与 tool-service 的 `TOOL__CONNECTOR_INTERNAL_SECRET`（留空则连接器工具不暴露，授权页面仍可用）。
  4. 重启 connector-service 与 tool-service。
- **替代方式（不想注册 public integration）**：用 Notion **internal integration token** 直接建连接——(1) Notion「Settings → Connections → Develop or manage integrations」新建 internal integration，复制 **Internal Integration Secret**（`ntn_…`）；(2) 在目标页面/数据库的「Connections / Share」里把该 integration 加进去（未 share 的资源会 403/404）；(3) Web `/connectors` → Notion 卡片「粘贴令牌」，或调 `POST /v1/connections`，body `{"provider":"notion","token":"<secret>","display_name":"可选"}` → 201。该路径**不需要** `CONNECTOR__NOTION_CLIENT_ID/SECRET`，也没有授权页/回调；连接 `auth_kind=token`、令牌走同一套信封加密落库，身份按 `integration:<bot_id>` 记录；token 无过期/刷新，调用遇 401 置 `expired`，重新粘贴新令牌即可。
- **执行链**：orchestrator → tool-service（`TOOL__CONNECTOR_*`，目录按租户缓存 30s）→ connector-service；工具名 `<Provider>__<tool>`。只读 4 个：`Notion__search`、`Notion__read_page`、`Notion__read_blocks`、`Notion__query_data_source`（`risk=read` 自动放行）；写 2 个：`Notion__create_page`、`Notion__append_blocks`（`risk=write`，对话里先挂起、用户就地确认后以 `approved=true` 重放，任务路径走 `approval_policies` 确认）。审计落 tool-service 的 `tool_calls`。
- **已删除**：IMAP/SMTP 邮件连接与收发、Gmail / Microsoft 365 OAuth 授权、日历事件 CRUD；存量连接由迁移统一撤销。

## 3.3 MCP Client（第三方工具）

- `/mcp` 页「我的 MCP」：注册 MCP server、测试/刷新工具、启用/停用、删除。交互是左侧列表 + 右侧定义详情（传输方式、地址/命令、参数、环境变量键名、超时、工具清单）。
- 传输二选一：`http` 是远程 Streamable HTTP（Server URL + 可选 Token）；`stdio` 由 orchestrator 沙箱拉起本地进程（命令/参数/环境变量/运行时/镜像都在页面里定义，命令须命中 allowlist）。沙箱不可用时测试会如实报错。
- 工具命名空间：`mcp__<server>__<tool>`；统一注入执行图，未信任 server 的工具按危险操作处理（需人工确认），开启「免确认执行」后按受控写自动执行。
- 「免确认执行」（`auto_approve`，默认关）是每个 server 的信任开关：开了它，工具在
  **对话**里可直接调用（对话没有审批流，见架构 §6.7），任务里也不再逐次确认；关着时
  对话里只会回喂「请改用任务」。注册表单与详情页都有这个开关。
- 对话里的显式引用：Web 对话页输入区在「技能」旁有「MCP」选择器（`?mcp=<server_id>` 可深链），选中后这条消息**只**装配被引用 server 的 MCP 工具；不选 = 全部 server（向后兼容）。引用随消息/run 落库（`mcp_server_ids`，最多 10 个）并进系统提示词，历史消息显示 `MCP · <server>` 徽标；run 建好后引用固化为快照，后续再改选择不影响已建 run。接口侧 `POST /v1/conversations/{id}/messages`（含 `/messages/stream`）与 `POST /v1/runs` 都接受 `mcp_server_ids`，校验在落库前完成：未知 404 `mcp.server_not_found`、停用 409 `mcp.server_disabled`、能力未启用 409 `mcp.disabled`。
- 凭证只写不读：http 的 token 用 `AGENT_ORCHESTRATOR__MCP_SECRET` 加密存 DB，读端只返回尾号；stdio 的 env 整体加密，读端只返回 `env_keys`。
- http 传输收到 401 时按「需要授权」处理：测试端点落 `last_status=auth_required`、`/mcp` 页标「需要授权」；工具调用不执行、回喂结构化错误（`code: mcp.auth_required`），响应头 `WWW-Authenticate` 保留下来作发现线索。
- 浏览器 OAuth 授权（P1，开关 `AGENT_ORCHESTRATOR__MCP_OAUTH_*`，默认开）：server 测试出现 `auth_required` 后，`/mcp` 详情卡出现「去授权」——orchestrator 先用 `WWW-Authenticate` 里的 `resource_metadata`（缺失则按 `<origin>/.well-known/oauth-protected-resource[<path>]`）做 RFC 9728 发现，再按 RFC 8414 / OIDC 元数据找授权服务器，没有预注册客户端时走 RFC 7591 动态注册；返回一次性授权链接（`state` 用 `MCP_SECRET` 签名，默认 10 分钟过期）。浏览器授权后由 **API 网关同源回调** `http://127.0.0.1:8007/api/mcp/oauth/callback` 收 `?code&state`，带 `X-Internal-Secret` 转给 orchestrator，用 PKCE `S256` 换 token（access/refresh 加密存 `mcp_server_auth` 表），再 302 回 `/mcp?oauth=success`。之后工具调用发现令牌过期时单飞刷新；刷新被判 `invalid_grant` 会清空令牌并重新要求授权。stdio server 不适用（凭据走 env），纯静态 token 的 http server 也不需要走这条链路。`MCP_OAUTH_REDIRECT_URI` 必须与 AS 上注册的回调完全一致。
- http 传输按 Streamable HTTP 规范回带会话头：`initialize` 响应里的 `Mcp-Session-Id` 会带到后续每个请求（含 notification），之后请求带协商出的 `MCP-Protocol-Version`；会话失效（服务端回 404）自动重新 initialize 并重试一次，关闭时尽力 `DELETE` 终止会话。
- 本地演示 server：`uv run python scripts/mock-mcp-server.py`（`http://127.0.0.1:8010/mcp`，提供 echo/add），在 `/mcp` 页注册后点「测试/刷新」。
- MCP Server 对外暴露（M3）尚未实现。

## 3.4 API Key

- 设置页「API Keys」创建/轮换/吊销;明文只在创建/轮换时返回一次,服务端只存 SHA-256 哈希。
- scopes:`mcp:tools:read`、`mcp:tools:call`、`artifacts:read`。
- 用 account-service 内部校验接口 `/v1/api-keys/verify`(X-Internal-Secret)。

## 3.5 公开 MCP Server（本地版）

- 端点:`POST http://127.0.0.1:8002/mcp`(Streamable HTTP;直接对 orchestrator,不经过 API 网关会话)。
- 鉴权:`Authorization: Bearer bm_live_...`(API Key)或 OAuth 2.1 access token;也支持 `X-API-Key`。
- 支持 `initialize`、`ping`、`tools/list`、`tools/call`、`resources/list`、`resources/read`。
- 公开面只暴露只读工具:`beauty.whoami`、`beauty.list_runs`、`beauty.get_artifact`,以及文档/知识库中的只读工具;受控写(技能执行、工作区文件)与危险操作(对外不可逆写)都不暴露。
- 资源:`beauty://artifacts/{artifact_id}`(仅数据库内联存储的 Markdown)。
- 本地示例:

```sh
KEY=bm_live_...  # 设置页创建
curl -s http://127.0.0.1:8002/mcp \
  -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}'
```

## 3.6 OAuth 2.1（MCP 客户端授权）

- 授权服务器元数据:`http://127.0.0.1:8001/.well-known/oauth-authorization-server`
- MCP 受保护资源元数据:`http://127.0.0.1:8002/.well-known/oauth-protected-resource`
- 流程:动态注册 `POST /v1/oauth/register` → 浏览器打开 `http://localhost:5173/oauth/authorize?...` 登录并确认 → 以 `code + PKCE S256` 换 `POST /v1/oauth/token` → 用 access token 调 `/mcp`。
- access/refresh token 都是不透明串,DB 只存 SHA-256;MCP Server 通过内部 `/v1/oauth/introspect` 换取租户/scopes。
- refresh token 旋转:旧 refresh 使用一次即失效。
- 撤销:标准 `POST /v1/oauth/revoke`;设置页「已授权应用」可查看/按客户端整体撤销。
- 动态注册限流:`ACCOUNT__OAUTH21_REGISTER_LIMIT_PER_HOUR`(进程内滑动窗口,默认 10/小时/IP)。
- 配置:`ACCOUNT__OAUTH21_WEB_ORIGIN`(授权页 origin)、`AGENT_ORCHESTRATOR__MCP_RESOURCE_URL`(受保护资源 URL)。

## 3.7 真实模型

- 在 services/llm-gateway/.env 配 LLM_GATEWAY**API_KEY,可选 LLM_GATEWAY**API_BASE 与 DEFAULT_MODEL。
- orchestrator 侧设 AGENT_ORCHESTRATOR\_\_LLM_ENABLED=true;需要工具调用时再开 TOOLS_ENABLED=true 与 MCP_ENABLED=true。
- `.env.example` 已把其余开发侧能力开关(工具/文档/知识库/outbox/交付物镜像/沙箱/技能执行/stdio MCP)默认置 true;唯一需要自己补的是模型密钥与本条的 `LLM_ENABLED=true`。
- 交付物(架构 §7.1):一个 run 可有多件,报告/技能产物/MCP 外链文件都算;工具返回的外链文件在**执行期**下载落库,开关与上限是 `AGENT_ORCHESTRATOR__ARTIFACT_DOWNLOAD_ENABLED` / `_MAX_FILES` / `_MAX_BYTES` / `_TIMEOUT_SECONDS`(默认 true / 10 件 / 10MB / 20s)。取件点:`GET /api/v1/runs/{id}/artifacts`(列表)与 `GET /api/v1/runs/{id}/artifacts/{aid}/download`(原始字节),工作区侧对应 `/api/v1/artifacts/{id}/download`;运行详情与「变更与产物」页可直接预览/下载。
- 验证:curl -s -H Content-Type:application/json -d {"messages":[{"role":"user","content":"只回复:模型正常"}]} http://127.0.0.1:8003/v1/completions。
- 真实模型工具名必须匹配 ^[a-zA-Z0-9_-]+$;MCP 工具使用 mcp**<server>**<tool>。

## 3.8 编码 Function Tools

- **内置工具服务 tool-service（:8008）**：P0 起工具目录与执行都委托给它，文件工具在 workspace-service 的
  沙箱内执行（不再直读服务进程本机目录）。开启方式：orchestrator 侧设
  `AGENT_ORCHESTRATOR__TOOL_SERVICE_ENABLED=true`（默认 false，compose 演示也默认关），并确认 tool-service 的
  `TOOL__WORKSPACE_SERVICE_URL` 指向 workspace-service；关掉即回到进程内工具（只剩文档/知识库与 MCP/skill，见下方「旧进程内路径」）。
- **工具面（P2 收口后）**：`read`/`write`/`edit`/`glob`/`grep`/`list_dir`/`search_knowledge`/`ask_question`；P3 起可选 `fetch`（只读抓公网文本，`TOOL__FETCH_ENABLED=true` 时出现在目录里，chat 与任务模式都可用）；对话只挂只读工具
  （`read`/`glob`/`grep`/`list_dir`/`search_knowledge`，开着 fetch 时再含 `fetch`），写工具与 `ask_question` 只在任务模式装配。
- **受控写（P1）**：`write`/`edit` 风险等级为 `write`；租户策略为 confirm 时先落 `pending_writes` 等人工确认，确认后
  `POST /runs/{id}/confirm` 再带 `approved=true` 执行；tool-service 侧未带 approved 的写请求一律 403 `tool.confirmation_required`。
- **读前置与限额**：同 run 内必须先 `read` 过目标路径（tool-service 的 `file_reads` 表），否则 `tool.read_required`；
  读后文件变化 → `tool.stale_read`；单 run 上限见 tool-service README（`TOOL__MAX_WRITES_PER_RUN` /
  `TOOL__MAX_WRITE_BYTES_PER_RUN` / `TOOL__MAX_TOOL_CALLS_PER_RUN`，默认 50 / 5MiB / 60）。
- **知识库检索**：`search_knowledge` 由 tool-service 以内部 JWT 代理到 knowledge-service（:8006），需
  `TOOL__HTTP_EXECUTOR_ENABLED=true`（默认 true）与 `TOOL__KNOWLEDGE_SERVICE_URL`；文档工具 `list_documents`/`read_document`
  仍在 orchestrator 进程内（随对象存储波次再迁）。
- **模型提问（P2）**：任务模式下模型可调 `ask_question`（1–4 题，单选/多选/自由输入），run 进 `awaiting_input` 并落
  `run.question.required.v1`；聊天页出现提问卡片，提交走 `POST /api/v1/runs/{id}/answer`
  （`{question_id, action: answer|decline, answers:[{index, labels, custom}]}`）；超时默认 900s
  （`AGENT_ORCHESTRATOR__QUESTION_TIMEOUT_SECONDS`，设 0 关闭），超时按 decline 继续执行。SSE 不新增帧类型。
- **旧进程内路径（P2 收口后）**：`code_list_files`/`code_read_file`/`code_search_text`/`code_write_file`/`code_edit_file`
  与 `AGENT_ORCHESTRATOR__CODE_WORKSPACE_ROOTS` 已删除；进程内只保留文档/知识库工具与 MCP/skill 工具。历史 run 的
  `skill_tools` 快照里的旧名不再解析。
- 公开 MCP Server 按 catalog 的「`kind=function` 且 `risk=read`」生成只读白名单，不暴露 `ask_question` 与写工具。
- Skills 的 `coding.repo-readonly-review` 已改绑新名 `glob`/`read`/`grep`。
- 设计与验收见 [design/tool-service.md](./design/tool-service.md)；端口 8008 见 §2 端口表与 `scripts/dev.sh`。

## 3.9 附件解析与上下文注入

- 支持 PDF(pypdf)、docx(python-docx)、xlsx(openpyxl)与文本类;上传时解析,单附件最多保留 20k 字符。
- 聊天附件:直接对话会把当前消息附件文本附到 user content;任务路径会把附件文本拼进 run goal,plan/execute 可见。
- 解析失败不拒绝附件,只记录 extraction_error;图片暂不 OCR。
- 解析器在 services/agent-orchestrator/agent_orchestrator/services/documents.py(AO 测试覆盖 PDF/docx/xlsx/文本);解析依赖(pypdf/python-docx/openpyxl)只由 AO 声明,common 不再继承。

## 4.0 Job worker（run 队列）

orchestrator 默认开启本地 worker(`AGENT_ORCHESTRATOR__JOBS_ENABLED=true`):

- run 的 plan/approve/confirm/resume/retry 先写 `jobs` 表,worker 轮询领取;
- 领取后置 running + lease(默认 60s),进程崩溃后 lease 过期会被重新领取;
- 失败按 attempts 指数退避,超过 `max_attempts` 落 failed 并记录 last_error;
- 测试夹具显式 `jobs_enabled=false`,保留同步语义。

- 计划审批默认自动放行:`AGENT_ORCHESTRATOR__PLAN_AUTO_APPROVE`(默认 true)——run 装配的工具(基础工具 + 引用的 MCP 工具,按技能 tools 白名单裁剪)没有 DANGER 级时,计划生成后自动放行直接执行(`run.planned.v1` 带 `auto_approved=true`,并补 `run.approved.v1`);装配了 DANGER 工具(未信任 MCP、对外不可逆操作)仍停在 `awaiting_approval` 等放行/拒绝。设为 false 回到"每次 plan 都人工审批";执行阶段危险操作前的确认闸(`awaiting_confirmation`)不受影响。

前端提交审批后会短暂看到原状态(自动放行的 run 没有这一步),worker 执行完下一次轮询即更新;这是队列化的预期行为。

## 4.1 用量 / 模型 / BYOK

- `/usage`：今日/本月 tokens、请求次数、按模型分布；没有数据时显示空态。
- 顶栏模型 chip 读取 llm-gateway 的 `/v1/models`（当前默认 `deepseek/deepseek-chat`）。
- 设置页 BYOK：**已参与模型调用**——保存后 llm-gateway 按租户向 account 解析密钥，命中即用租户 key、否则回退平台 `LLM_GATEWAY__API_KEY`；列表只回掩码与末 4 位。契约见 [design/byok.md](./design/byok.md)。
- 本地没有模型 key 时用量页为空是正常现象；可用 account-service 的内部上报接口造数：

```sh

# 需要 ACCOUNT\_\_INTERNAL_SECRET

curl -X POST http://127.0.0.1:8001/v1/usage/events \
 -H "Authorization: Bearer <access-token>" \
 -H "X-Internal-Secret: <internal-secret>" \
 -H 'Content-Type: application/json' \
 -d '{"model":"deepseek/deepseek-chat","prompt_tokens":1200,"completion_tokens":400}'
```

BYOK 本地联调要点（契约见 [design/byok.md](./design/byok.md)）：

1. 三个服务配**同一个**内部密钥再重启：`ACCOUNT__INTERNAL_SECRET`、`LLM_GATEWAY__INTERNAL_SECRET`、`KNOWLEDGE__INTERNAL_SECRET`。任一处留空都等于 BYOK 静默停用（account 内部端点关闭 / 网关不解析）。
2. 设置页保存一把 key（等价 `PUT /v1/byok/deepseek`），列表只显示 `••••` + 末 4 位。
3. 发一条对话，llm-gateway 日志出现 `byok_resolved` 且 `key_hint` 与录入一致即生效；只有 `llm_byok_lookups_total{status="negative"}` 抬头，说明该 provider 没配上（provider 名要与 `model` 前缀一致）。
4. 回退验证：`LLM_GATEWAY__BYOK_ENABLED=false` 重启网关，所有租户立即回平台 key，account 数据不动。

```sh
# 写入(access token 来自登录;provider 取模型前缀)
curl -X PUT http://127.0.0.1:8001/v1/byok/deepseek \
 -H "Authorization: Bearer <access-token>" \
 -H 'Content-Type: application/json' \
 -d '{"api_key":"sk-..."}'

# 验证明文解析(仅服务间;两个头都要,internal-secret 与 account/gateway 同值)
curl http://127.0.0.1:8001/v1/byok/deepseek/secret \
 -H "X-Internal-Secret: <internal-secret>" \
 -H "X-Tenant-Id: <tenant-id>"
```

## 5. 演示数据

```sh
npm run dev:seed
```

会创建/复用一个本地演示账号,并写入:

- 2 个会话
- Markdown / Diff / 图片产物
- 1 个演示 run(LLM 关闭时走确定性桩)

脚本是幂等的:同标题会话/产物已存在会跳过。

默认账号:

```text
demo@example.com / DemoPass123!
```

可用环境变量覆盖:SEED_EMAIL、SEED_PASSWORD。

## 6. 重置本地数据

```sh
npm run dev:reset          # 交互确认
npm run dev:reset -- --yes # 不确认
```

会删除:

- services/_/_.db(SQLite)
- services/\*/.artifacts(本地产物)
- .cache/dev(日志)

保留 .env 与源码。服务运行中会拒绝删除;先 Ctrl+C 停 dev.sh,或加 --force。
重置后重新 npm run dev,启动前会自动按 alembic 迁移建表(create_all 仍是兜底)。

## 7. 日志与排障

| 现象                   | 处理                                                                                                                                                                                                                                                                         |
| ---------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 端口被占用             | scripts/dev.sh 会跳过;确认是否已有旧进程,必要时手动 kill                                                                                                                                                                                                                     |
| 前端 401/502           | 检查 API 网关 :8007 是否启动;npm run dev:status                                                                                                                                                                                                                              |
| 日志大量 502           | 服务间调用全走 127.0.0.1,本机开着系统代理(macOS 上 Clash 等的 HTTP 代理)时,Python httpx 会继承系统代理且不认「绕过代理」列表,请求被代理转发后返回 502(日志报 auth.jwks_unavailable);npm run dev 已自动设置 NO_PROXY,手动启动服务时先 export NO_PROXY=127.0.0.1,localhost,::1 |
| 登录后页面空           | 检查 account-service :8001 与 API 网关日志                                                                                                                                                                                                                                   |
| 会话接口 404/旧结构    | 重启 agent-orchestrator,确认它跑的是当前代码                                                                                                                                                                                                                                 |
| 官网打不开             | 单独 npm run dev:site(SPA 里「官网」跳 :3000)                                                                                                                                                                                                                                |
| SQLite 表结构不对      | 启动时已自动迁移;仍报 no such column/迁移提示结构漂移时先 npm run dev:migrate 看原因,确实无法对齐再 npm run dev:reset                                                                                                                                                        |
| 启动报缺 `aiosqlite`   | 共享 venv 的服务 dev 依赖被根 `uv sync` 或单项目 `uv run` 剪掉了;`npx nx run agent-orchestrator:sync --args="--all-packages --all-groups"` 补齐(`npm run dev` 启动前会自动检测并同步)                                                                                        |
| 想用真实模型           | 配 LLM_GATEWAY\_\_API_KEY(+可选 API_BASE)后重启 llm-gateway                                                                                                                                                                                                                  |
| BYOK 不生效/烧平台 key | 三处 internal_secret 必须同一值（account/gateway/knowledge）；容器内 `ACCOUNT_BASE_URL` 要指向 `http://account-service:8001`；看 `llm_byok_lookups_total{status}` 与 `byok_lookup_failed` 日志                                                                               |
| 附件上传失败           | 检查大小 ≤10MB、类型(文本/Markdown/CSV/JSON/PDF/图片)与 orchestrator 是否最新                                                                                                                                                                                                |
| 要串一条请求链路       | 浏览器 Network 看响应头 `X-Request-ID`,再在服务日志里筛 `request_id="..."`;规则见 `docs/observability.md` §5                                                                                                                                                                 |
| 想强开 JSON 日志       | `<SERVICE>__LOG_JSON=true`(dev 默认人读格式);关访问日志用 `<SERVICE>__ACCESS_LOG_ENABLED=false`,慢请求阈值 `<SERVICE>__SLOW_REQUEST_MS`                                                                                                                                      |
| 想看指标               | 抓 `GET /internal/metrics`(Prometheus 文本,只在服务进程内,不经 API 网关);关掉用 `<SERVICE>__METRICS_ENABLED=false`,要令牌用 `<SERVICE>__METRICS_TOKEN`                                                                                                                       |
| 想接 Sentry            | 配 `<SERVICE>__SENTRY_DSN`(+可选 `SERVICE__SENTRY_RELEASE`);不配则完全不初始化,内网形态零副作用                                                                                                                                                                              |
| 想看链路追踪           | 配 `<SERVICE>__OTLP_ENDPOINT`(完整 `…/v1/traces`,OTLP/HTTP)才会导出;不配则 span 只在进程内,日志里用 `trace_id`/`span_id` 串联。采样 `<SERVICE>__TRACE_SAMPLE_RATIO`(默认 0.1),关掉 `<SERVICE>__TRACING_ENABLED=false`                                                        |
| 想看模型请求/正文      | 网关日志里 `llm_request`/`llm_response` 带 provider、model、token 用量与耗时；要看 prompt/响应正文置 `LLM_GATEWAY__LOG_CONTENT=true`（各截断 2KB，含用户数据，仅本地开）                                                                                                     |

### 7.1 本地数据库迁移(自动)

npm run dev 启动前会调用 scripts/migrate-local.sh(也可单独执行 npm run dev:migrate),把 6 个带 alembic 迁移的服务的 SQLite 库推到 head,失败则中止启动:

- 空库/库不存在:从零迁移到 head;
- 已登记 alembic_version:补齐落后的 revision;
- create_all 建的旧库(无版本记录):先在临时副本上 stamp head + alembic check,结构一致才给真实库登记基线(收养),不一致则不动真实库并报错;
- 非 SQLite(如 Postgres):跳过并提示手动 `alembic upgrade head`。

它只负责「应用迁移」,不生成迁移:改 ORM 表结构仍必须新增 alembic 迁移脚本(见 AGENTS.md 的模型/迁移条目)。

## 8. 测试

```sh
npx nx test @beauty/web -- --run
npx nx test api-gateway
npx nx test account-service
npx nx test agent-orchestrator
npx nx typecheck @beauty/web
npx nx lint @beauty/web
```

注意:不带 -- --run 的 npx nx test 是 vitest watch 模式,会挂住。

### 8.1 黄金 run(进程内全链路冒烟)

跨重构(R-27 graph 去反向依赖、R-28 拆 services/runs.py、R-29 API 瘦身)只允许改内部结构,不允许改对外 HTTP 契约与 SSE 事件序列。黄金 run 用 TestClient 在**进程内**驱动 agent-orchestrator 真实应用(不起端口、不依赖已启动的服务与真实模型),走完「健康探针 → 会话 → 流式对话 → 复杂任务建 run → 计划审批 → 危险操作确认 → 交付物 → 事件序列 → sqlite 落库」全链路并逐阶段断言:

```sh
bash scripts/golden-run/golden-run.sh           # 临时 sqlite,跑完清理;退出码 0=全 PASS,1=有 FAIL
bash scripts/golden-run/golden-run.sh --keep    # 保留库到 .cache/golden-run/<UTC 时间戳>/golden.db,便于对照排查
```

临时 sqlite + 脚本化 FakeLLM + 合成 DANGER 工具 + 内存假知识库,零网络(进程内 socket 守卫兜底)、零 Docker/Postgres/Redis 依赖,约 0.2s 跑完。用法与维护约定见 `scripts/golden-run/README.md`。

## 9. 暂不包含

- 容器化/云 PG/云 Redis/部署
- 真实 Google/GitHub OAuth 凭据
- Sentry、对象存储、沙箱执行、MCP
