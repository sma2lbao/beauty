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
npm run dev            # 六个业务服务 + BFF + Vite SPA
npm run dev:site       # 额外启动 Next.js 官网(:3000)
npm run dev:status     # 只检查健康状态
```

脚本行为:

- 启动顺序并行;已有端口被占用则跳过并提示(不会杀已有进程);
- 缺 .env 自动创建;
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
| bff                | 8007 | /api/health |
| web                | 5173 | /           |
| site(可选)         | 3000 | /           |

connector-service(:8004)是连接器框架,当前无内置连接器(邮件与日历能力均已下线),端口保留。

打开 http://localhost:5173。

**BFF(:8007)是必需的**:Vite 的 /api 已统一代理到 BFF,BFF 再转发
orchestrator/account/workspace/knowledge。忘记启动 BFF 时前端会表现为 401/502。

## 4. 鉴权模式

本地默认 VITE_AUTH_MODE=token:

- 登录走 account-service 邮箱+密码;
- 业务请求走 /api → BFF(Bearer 直通兜底);
- 不依赖 Google/GitHub 凭据。

需要验证 Cookie 会话时,把 apps/web/.env.local 设为:

```env
VITE_AUTH_MODE=session
```

然后重启 Vite。session 模式下 BFF 会写加密 httpOnly Cookie,SPA 不接触 token。
OAuth 按钮只有在 account-service 配了 client id/secret 时才可用;本地不配也可用
邮箱+密码登录。

## 3.1 Skills（场景模板）

- 内置 manifest 在仓库根 `skills/*.json`（id/version/title/workspace_type/tools/system_prompt 等）。
- orchestrator 启动时加载，暴露 `GET /v1/skills`、`GET /v1/skills/{id}`。
- `POST /v1/runs` 或发消息时可带 `skill_id`：创建 run 时冻结 skill 快照（系统提示词 + 工具白名单），后续改 manifest 不影响已建 run。
- 执行时只暴露白名单内工具；例如知识库类技能只能 `search_knowledge`、`list_documents`/`read_document`，只读代码审查技能只能 `code_list_files`/`code_read_file`/`code_search_text`，不能写。
- Web `/skills` 可浏览技能；任务台输入区的「技能」选择器也可直接绑定。

## 3.2 连接器（空框架）

- connector-service(:8004)只保留连接器框架：邮件与日历能力均已下线，当前无内置连接器；Web 端也不再有连接器页面。
- 保留接口：`GET /v1/connections`（列表）、`GET /v1/connections/{id}`（详情）、`DELETE /v1/connections/{id}`（撤销：留痕不物理删，重复撤销 409）、`GET /healthz`。
- 已删除：IMAP/SMTP 邮件连接与收发、Gmail / Microsoft 365 OAuth 授权、日历事件 CRUD。
- 不再需要 Google / Microsoft client id/secret 与 OAuth 回调配置；存量连接已由迁移统一撤销。

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
- 浏览器 OAuth 授权（P1，开关 `AGENT_ORCHESTRATOR__MCP_OAUTH_*`，默认开）：server 测试出现 `auth_required` 后，`/mcp` 详情卡出现「去授权」——orchestrator 先用 `WWW-Authenticate` 里的 `resource_metadata`（缺失则按 `<origin>/.well-known/oauth-protected-resource[<path>]`）做 RFC 9728 发现，再按 RFC 8414 / OIDC 元数据找授权服务器，没有预注册客户端时走 RFC 7591 动态注册；返回一次性授权链接（`state` 用 `MCP_SECRET` 签名，默认 10 分钟过期）。浏览器授权后由 **BFF 同源回调** `http://127.0.0.1:8007/api/mcp/oauth/callback` 收 `?code&state`，带 `X-Internal-Secret` 转给 orchestrator，用 PKCE `S256` 换 token（access/refresh 加密存 `mcp_server_auth` 表），再 302 回 `/mcp?oauth=success`。之后工具调用发现令牌过期时单飞刷新；刷新被判 `invalid_grant` 会清空令牌并重新要求授权。stdio server 不适用（凭据走 env），纯静态 token 的 http server 也不需要走这条链路。`MCP_OAUTH_REDIRECT_URI` 必须与 AS 上注册的回调完全一致。
- http 传输按 Streamable HTTP 规范回带会话头：`initialize` 响应里的 `Mcp-Session-Id` 会带到后续每个请求（含 notification），之后请求带协商出的 `MCP-Protocol-Version`；会话失效（服务端回 404）自动重新 initialize 并重试一次，关闭时尽力 `DELETE` 终止会话。
- 本地演示 server：`uv run python scripts/mock-mcp-server.py`（`http://127.0.0.1:8010/mcp`，提供 echo/add），在 `/mcp` 页注册后点「测试/刷新」。
- MCP Server 对外暴露（M3）尚未实现。

## 3.4 API Key

- 设置页「API Keys」创建/轮换/吊销;明文只在创建/轮换时返回一次,服务端只存 SHA-256 哈希。
- scopes:`mcp:tools:read`、`mcp:tools:call`、`artifacts:read`。
- 用 account-service 内部校验接口 `/v1/api-keys/verify`(X-Internal-Secret)。

## 3.5 公开 MCP Server（本地版）

- 端点:`POST http://127.0.0.1:8002/mcp`(Streamable HTTP;直接对 orchestrator,不经过 BFF 会话)。
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

- 配 AGENT_ORCHESTRATOR\_\_CODE_WORKSPACE_ROOTS(JSON 数组;绝对路径,或相对服务进程 cwd 的路径——示例为 `../..` 即仓库根)后,Agent 获得:code_list_files / code_read_file / code_search_text(只读)、code_write_file / code_edit_file(受控写,在 roots 内自动执行,敏感路径写前拦截)。
- 路径 resolve 后必须仍在 root 内;.git/.env/node_modules 等禁止写;不提供 shell。
- 公开 MCP Server 不暴露 code\_ 前缀工具,避免 API Key 读服务端本地文件。
- Skills 的 coding.repo-readonly-review 已绑定只读代码工具。

## 3.9 附件解析与上下文注入

- 支持 PDF(pypdf)、docx(python-docx)、xlsx(openpyxl)与文本类;上传时解析,单附件最多保留 20k 字符。
- 聊天附件:直接对话会把当前消息附件文本附到 user content;任务路径会把附件文本拼进 run goal,plan/execute 可见。
- 解析失败不拒绝附件,只记录 extraction_error;图片暂不 OCR。
- 解析器在 packages/common/common/documents.py(common 测试覆盖 PDF/docx/xlsx/文本)。

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
- 设置页 BYOK：应用层加密存储，只显示尾号；**暂不参与模型调用**。
- 本地没有模型 key 时用量页为空是正常现象；可用 account-service 的内部上报接口造数：

```sh

# 需要 ACCOUNT\_\_INTERNAL_SECRET

curl -X POST http://127.0.0.1:8001/v1/usage/events \
 -H "Authorization: Bearer <access-token>" \
 -H "X-Internal-Secret: <internal-secret>" \
 -H 'Content-Type: application/json' \
 -d '{"model":"deepseek/deepseek-chat","prompt_tokens":1200,"completion_tokens":400}'
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
重置后重新 npm run dev,服务会在 AUTO_CREATE_TABLES=true 时自动建表。

## 7. 日志与排障

| 现象                | 处理                                                                                                                                                                                                                  |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 端口被占用          | scripts/dev.sh 会跳过;确认是否已有旧进程,必要时手动 kill                                                                                                                                                              |
| 前端 401/502        | 检查 BFF :8007 是否启动;npm run dev:status                                                                                                                                                                            |
| 登录后页面空        | 检查 account-service :8001 与 BFF 日志                                                                                                                                                                                |
| 会话接口 404/旧结构 | 重启 agent-orchestrator,确认它跑的是当前代码                                                                                                                                                                          |
| 官网打不开          | 单独 npm run dev:site(SPA 里「官网」跳 :3000)                                                                                                                                                                         |
| SQLite 表结构不对   | npm run dev:reset 后重启                                                                                                                                                                                              |
| 想用真实模型        | 配 LLM_GATEWAY\_\_API_KEY(+可选 API_BASE)后重启 llm-gateway                                                                                                                                                           |
| 附件上传失败        | 检查大小 ≤10MB、类型(文本/Markdown/CSV/JSON/PDF/图片)与 orchestrator 是否最新                                                                                                                                         |
| 要串一条请求链路    | 浏览器 Network 看响应头 `X-Request-ID`,再在服务日志里筛 `request_id="..."`;规则见 `docs/observability.md` §5                                                                                                          |
| 想强开 JSON 日志    | `<SERVICE>__LOG_JSON=true`(dev 默认人读格式);关访问日志用 `<SERVICE>__ACCESS_LOG_ENABLED=false`,慢请求阈值 `<SERVICE>__SLOW_REQUEST_MS`                                                                               |
| 想看指标            | 抓 `GET /internal/metrics`(Prometheus 文本,只在服务进程内,不经 BFF);关掉用 `<SERVICE>__METRICS_ENABLED=false`,要令牌用 `<SERVICE>__METRICS_TOKEN`                                                                     |
| 想接 Sentry         | 配 `<SERVICE>__SENTRY_DSN`(+可选 `SERVICE__SENTRY_RELEASE`);不配则完全不初始化,内网形态零副作用                                                                                                                       |
| 想看链路追踪        | 配 `<SERVICE>__OTLP_ENDPOINT`(完整 `…/v1/traces`,OTLP/HTTP)才会导出;不配则 span 只在进程内,日志里用 `trace_id`/`span_id` 串联。采样 `<SERVICE>__TRACE_SAMPLE_RATIO`(默认 0.1),关掉 `<SERVICE>__TRACING_ENABLED=false` |
| 想看模型请求/正文   | 网关日志里 `llm_request`/`llm_response` 带 provider、model、token 用量与耗时；要看 prompt/响应正文置 `LLM_GATEWAY__LOG_CONTENT=true`（各截断 2KB，含用户数据，仅本地开）                                              |

## 8. 测试

```sh
npx nx test @beauty/web -- --run
npx nx test bff
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
