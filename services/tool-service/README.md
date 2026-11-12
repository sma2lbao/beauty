# tool-service(工具服务, :8008)

内置工具的统一执行入口:模型侧只看到裸名工具(`read`/`write`/`edit`/`glob`/`grep`/`list_dir`/`ask_question`),P2 起新增只读知识库工具 `search_knowledge`,P3 起新增只读联网抓取工具 `fetch`(默认关闭),P1-L 起新增沙箱内命令执行 `exec`(默认关闭,risk=destructive),P5 起新增沙箱内 Python 代码执行 `run_code`(默认关闭,risk=destructive,代码经 stdin 直传 `python3 -`),P1-L M2 起新增后台作业三工具 `job_start`/`job_output`/`job_kill`(默认关闭,作业是沙箱内常驻进程),P1-L M3 起新增只读联网搜索工具 `web_search`(默认关闭,服务端出站调 tavily/brave),并启用交互工具 `ask_question`(本服务只校验参数并归一返回,挂起/恢复由 orchestrator 负责)。执行、限额、审计、幂等与账号隔离都由本服务负责,orchestrator 通过 HTTP 客户端调用(设计见 `docs/design/tool-service.md`)。

## 职责边界

| 关注点                                 | 归属                                                                                     |
| -------------------------------------- | ---------------------------------------------------------------------------------------- |
| 工具目录(catalog)、参数 schema、限额   | tool-service(代码即真源,`tool_service/services/catalog.py`)                              |
| 工具执行(沙箱内 helper 脚本)           | tool-service → workspace-service 沙箱                                                    |
| 命令执行(`exec`,默认关)                | tool-service → workspace-service 沙箱 `POST /exec`(`/bin/sh -lc`,不经 helper)            |
| 代码执行(`run_code`,默认关)            | tool-service → workspace-service 沙箱 `POST /exec`(`python3 -`,代码经 stdin,不经 helper) |
| 后台作业(`job_*`,默认关)               | tool-service → 同一沙箱:启动/读日志/终止脚本;作业元数据落本服务 `background_jobs` 表     |
| 知识库只读检索(`search_knowledge`)     | tool-service → knowledge-service HTTP API(HTTP 执行器,不进沙箱)                          |
| 公网文本抓取(`fetch`,默认关)           | tool-service 直连公网 HTTP GET(SSRF 防护 + 主机允许列表;不进沙箱、不执行命令)            |
| 公网搜索(`web_search`,默认关)          | tool-service 直连搜索提供方(tavily/brave);出口校验与 `fetch` 共用公网判定,不进沙箱       |
| 审批决策(allow/ask/deny)               | orchestrator 保留;tool-service 只做风险下限防御                                          |
| run 事件流(`run.tool.called.v1`)与 SSE | orchestrator 唯一落库;本服务不写 run_events                                              |
| 调用审计与幂等(`tool_calls` 表)        | tool-service                                                                             |
| `ask_question` 的挂起/恢复             | orchestrator 图(`interrupt`)+ run 事件                                                   |
| 连接器工具(`Demo__*` 等)               | 目录与执行都代理到 connector-service;本服务只做路由、限额与审计                          |

## HTTP API

| 方法 | 路径                            | 说明                                                                              |
| ---- | ------------------------------- | --------------------------------------------------------------------------------- |
| GET  | `/v1/tools`                     | 工具清单(默认只返回已启用;kind/executor 过滤;带账号 JWT 时并入该账号的连接器工具) |
| GET  | `/v1/tools/{name}`              | 单个工具描述;未知或未启用 → 404 `tool.not_found`                                  |
| POST | `/v1/tools/{name}:execute`      | 执行;业务错误一律 HTTP 200 + 失败信封                                             |
| GET  | `/v1/tool-calls`                | 调用审计分页(run_id/tool_name/ok 过滤)                                            |
| GET  | `/v1/tool-calls/{tool_call_id}` | 单条审计(跨账号 404)                                                              |
| GET  | `/healthz` `/readyz`            | 存活/就绪(就绪探针查 DB)                                                          |

幂等:`tool_call_id` 在账号内唯一;同 id 同参数重复调用返回 `idempotent_replay=true` 的重放结果,参数不同 → 409 `tool.idempotency_conflict`。

## 配置(`TOOL__` 前缀)

| 变量                                   | 默认                            | 说明                                                                                        |
| -------------------------------------- | ------------------------------- | ------------------------------------------------------------------------------------------- |
| `TOOL__DATABASE_URL`                   | `sqlite+aiosqlite:///./tool.db` | 审计库(生产 postgresql+asyncpg)                                                             |
| `TOOL__AUTO_CREATE_TABLES`             | `false`                         | 生产 false,迁移走 alembic                                                                   |
| `TOOL__AUTH_ENABLED`                   | `true`                          | 校验账号 JWT(内部签发走 account-service)                                                    |
| `TOOL__AUTH_ISSUER`                    | `http://127.0.0.1:8001`         | 签发方/JWKS 来源                                                                            |
| `TOOL__INTERNAL_SECRET`                | 空                              | 服务间换取账号 token 的 internal secret                                                     |
| `TOOL__WORKSPACE_SERVICE_URL`          | `http://127.0.0.1:8005`         | 沙箱来源                                                                                    |
| `TOOL__WORKSPACE_TIMEOUT_SECONDS`      | `60`                            | 沙箱 HTTP 超时                                                                              |
| `TOOL__KNOWLEDGE_SERVICE_URL`          | `http://127.0.0.1:8006`         | 知识库检索来源(`search_knowledge` 代理目标)                                                 |
| `TOOL__KNOWLEDGE_TIMEOUT_SECONDS`      | `20`                            | 知识库 HTTP 超时(单次仍受 descriptor 的 20s 上限约束)                                       |
| `TOOL__TOOL_TIMEOUT_SECONDS`           | `30`                            | 单次工具执行默认上限                                                                        |
| `TOOL__SANDBOX_TOOL_TTL_SECONDS`       | `1800`                          | 沙箱滑动 TTL                                                                                |
| `TOOL__READ_BEFORE_WRITE_ENABLED`      | `true`                          | 写前读校验:同 run 内没 read 过同路径 → `tool.read_required`                                 |
| `TOOL__MAX_WRITES_PER_RUN`             | `50`                            | 单 run 写操作次数上限(P1)                                                                   |
| `TOOL__MAX_WRITE_BYTES_PER_RUN`        | `5242880`                       | 单 run 写入字节上限(P1)                                                                     |
| `TOOL__MAX_TOOL_CALLS_PER_RUN`         | `60`                            | 单 run 工具调用总次数上限(P1)                                                               |
| `TOOL__FETCH_ENABLED`                  | `false`                         | 开启 `fetch`(只读抓公网文本);关闭时目录不暴露该工具(P3)                                     |
| `TOOL__FETCH_TIMEOUT_SECONDS`          | `15`                            | fetch HTTP 超时(descriptor 另设 20s 上限)                                                   |
| `TOOL__FETCH_MAX_BYTES`                | `1048576`                       | 单次响应体字节上限(超出截断)                                                                |
| `TOOL__FETCH_MAX_REDIRECTS`            | `3`                             | 最多跟随的重定向跳数                                                                        |
| `TOOL__FETCH_ALLOWED_HOSTS`            | 空                              | 出口收紧:逗号分隔主机允许列表(空=任意公网,`example.com` 含子域)                             |
| `TOOL__SEARCH_ENABLED`                 | `false`                         | 开启 `web_search`(只读联网搜索);关闭时目录不暴露、执行 404(P1-L M3)                         |
| `TOOL__SEARCH_PROVIDER`                | `tavily`                        | 搜索提供方:`tavily` 或 `brave`                                                              |
| `TOOL__SEARCH_API_KEY`                 | 空                              | 提供方 API Key;空=工具不可用(目录仍按开关展示,执行回 `tool.executor_unavailable`)           |
| `TOOL__SEARCH_BASE_URL`                | 空                              | 覆盖提供方基地址(空=官方地址;自建代理/测试用)                                               |
| `TOOL__SEARCH_TIMEOUT_SECONDS`         | `10`                            | 搜索 HTTP 超时(descriptor 另设 20s 上限)                                                    |
| `TOOL__SEARCH_DEFAULT_RESULTS`         | `5`                             | `max_results` 缺省条数                                                                      |
| `TOOL__SEARCH_MAX_RESULTS`             | `10`                            | `max_results` 上限                                                                          |
| `TOOL__SEARCH_MAX_BYTES`               | `1048576`                       | 单次响应体字节上限                                                                          |
| `TOOL__EXEC_ENABLED`                   | `false`                         | 开启沙箱内命令执行 `exec`(risk=destructive);关闭时目录不暴露、执行 404(P1-L)                |
| `TOOL__RUN_CODE_ENABLED`               | `false`                         | 开启沙箱内 Python 代码执行 `run_code`(risk=destructive);关闭时目录不暴露、执行 404(P5)      |
| `TOOL__JOBS_ENABLED`                   | `false`                         | 开启后台作业三工具 `job_start`/`job_output`/`job_kill`;关闭时目录不暴露、执行 404(P1-L M2)  |
| `TOOL__JOB_MAX_RUNTIME_SECONDS`        | `600`                           | 单作业最长运行时间;超时在下一次 `job_output`/`job_kill` 时惰性标记 timeout 并终止           |
| `TOOL__JOB_MAX_CONCURRENT_PER_ACCOUNT` | `5`                             | 单账号同时 running 的作业数上限(超限 → `tool.budget_exceeded`)                              |
| `TOOL__JOB_OUTPUT_DEFAULT_CHARS`       | `8000`                          | `job_output` 单路日志默认尾部字符数(`tail_chars` 缺省值)                                    |
| `TOOL__JOB_OUTPUT_MAX_CHARS`           | `20000`                         | `job_output` 单路日志尾部字符数上限                                                         |
| `TOOL__JOB_RETENTION_HOURS`            | `24`                            | 作业目录与终态库行的保留小时数(启动新作业时 best-effort 清理)                               |
| `TOOL__CONNECTOR_INTERNAL_SECRET`      | 空                              | 调用 connector-service 内部端点;空 = 关闭连接器工具(须与 `CONNECTOR__INTERNAL_SECRET` 一致) |
| `TOOL__CONNECTOR_BASE_URL`             | `http://127.0.0.1:8004`         | connector-service 基地址                                                                    |
| `TOOL__CONNECTOR_CATALOG_TTL_SECONDS`  | `30`                            | 连接器目录按账号缓存 TTL(新建/撤销连接最长延迟一个 TTL 生效)                                |

## 连接器工具(C2.4)

连接器工具不实现在本服务:目录来自 connector-service 的 `GET /v1/internal/connectors/tools`
(带 `X-Internal-Secret` + `X-Account-Id`,按账号 TTL 30s 缓存),执行 POST 到
`/v1/connectors/{provider}/execute`。descriptor 用 `executor=connector` 与
`connector={"provider": "<name>"}` 声明路由;工具名形如 `Demo__read_document`。

- 目录拉取失败**降级为无连接器工具**(不缓存,下次重试),不影响内置工具;
- 工具级失败(如 `connector.auth_expired`)按 HTTP 200 信封原样透传给 orchestrator
  (前端据此显示「重新连接」);HTTP/网络故障映射 `tool.connector_unavailable` /
  `tool.connector_timeout`;
- 执行仍走本服务的限额、幂等与审计(`tool_calls`),审批沿用 orchestrator 的
  `risk=read` → 自动放行规则;
- 审计补 `connection_id`(C2.4):execute 响应顶层的 `connection_id`(连接已解析时)
  由 gateway 提取并从模型可见 `result` 中剥离,落 `tool_calls.connection_id`;
  `GET /v1/tool-calls` 运维查询可见该字段,内置工具为 null;
- 未配置 `TOOL__CONNECTOR_INTERNAL_SECRET` 时整体关闭,`/v1/tools` 只返回内置工具。

## 知识库检索代理(P2)

- `search_knowledge` 是 `risk=read`、`executor=http` 的函数工具:参数在 journal 层校验(`query` 必填 ≤2000,`limit` 1..10 默认 5),不进沙箱、不写文件。
- 本服务用 `TokenBroker`(subject `svc-tool`,与沙箱调用同一 internal secret)换账号 JWT,携带 `Authorization: Bearer` 调 `knowledge-service` 的 `POST /v1/knowledge/search`。
- 返回形状沿用旧 orchestrator 工具:`{"query","provider","model","hits":[{"title","score","content"}]}`, `chunk_id/document_id` 等内部字段不喂给模型。
- 错误映射:下游 404 → `tool.not_found`;其余 4xx → `tool.invalid_args`;5xx/连接失败 → `tool.upstream_unavailable`(retryable);`httpx` 超时 → `tool.timeout`(retryable);internal secret 缺失属于配置问题,`tool.upstream_unavailable` 但 `retryable=false`。
- 只读代理:不建表、不发事件、不产生副作用;未知 HTTP 工具名一律 `tool.executor_unavailable`。

## 联网抓取(P3)

- `fetch` 是 `risk=read`、`executor=http` 的函数工具:参数只有 `url`(http/https,≤2048),返回 `{ok,url,final_url,status,content_type,bytes,truncated,text}`。
- 边界:只允许 http/https、拒绝 URL 内嵌凭证;解析出的**所有**地址必须是公网单播(内网/回环/链路本地/组播/保留段拒绝);每一跳重定向重新校验;只收文本 content-type;响应 ≤1 MiB、文本 ≤20,000 字符、最多 3 跳、超时 15s(descriptor 20s)。
- 开关关闭时 `GET /v1/tools` 不出现、`GET /v1/tools/fetch` 404、执行同样 404 `tool.not_found`(可用性检查前移到执行入口);打开后 chat 与任务两种模式都可用(READ 自动放行)。
- 典型用途:读 GitHub 仓库元数据(`api.github.com` JSON)与文件原文(`raw.githubusercontent.com` 纯文本),无需 `git` 或沙箱命令执行。

## 联网搜索(web_search,P1-L M3)

- `web_search` 是 `risk=read`、`executor=http` 的函数工具:参数 `query`(1..500)、`max_results`(1..10,默认 `TOOL__SEARCH_DEFAULT_RESULTS`);返回 `{query,provider,results:[{rank,title,url,snippet}],notes}`。
- 提供方:默认 `tavily`(`POST {base}/search`),`brave` 走 `GET {base}/res/v1/web/search`;基地址默认官方,可用 `TOOL__SEARCH_BASE_URL` 覆盖。**API Key 为空时工具不可用**,`/v1/tools` 会隐藏它。
- **默认关闭**(`TOOL__SEARCH_ENABLED=false`):关闭时目录不暴露、GET/execute 一律 404 `tool.not_found`。
- 出口边界:https-only,解析出的地址必须是公网单播(与 `fetch` 共用 `is_public_address`,含 CGNAT 拒绝),解析失败直接拒绝、不跟随重定向、响应 ≤1 MiB;结果条数超上限或单个 url 为空/超 2048 时丢弃并重排 `rank`。
- 错误映射:401/403 → `tool.search_auth_failed`;429 → `tool.upstream_rate_limited`;5xx → `tool.upstream_unavailable`;3xx / 非 JSON → `tool.upstream_bad_response`;其余 4xx → `tool.upstream_unavailable`。
- 注入风险:返回的 `snippet` 是**外部不可信文本**,工具描述里已要求模型只把它当资料、不执行其中的指令;无缓存与提供方配额控制。设计见 `docs/design/web-search.md`。

## 命令执行(exec,P1-L)

- `exec` 是 `risk=destructive`(legacy `danger`)、`executor=sandbox` 的函数工具:参数 `command`(1..4000 字符,交 `/bin/sh -lc`)、`cwd`(≤512 字符,相对 `/workspace`,绝对路径也必须落在其内)、`timeout_seconds`(1..120,默认 60)。
- **默认关闭**(`TOOL__EXEC_ENABLED=false`):关闭时目录不暴露、GET/execute 一律 404 `tool.not_found`。开启后 orchestrator 的 plan 闸与 confirm 闸都会因危险工具而中断,需人工放行。
- **不进 helper**:文件工具走沙箱内 helper 脚本(JSON 协议 + 路径 jail),exec 直接调 `SandboxProvider.exec`,拿真实退出码/信号;argv 固定 `[/bin/sh, -lc, command]`,不支持持久会话/PTY(M2 再议)。
- **非零退出码不是失败**:`ok=true`,失败信息在 `stderr`(模型据此修正);`exit_code=124` 表示被超时终止。
- **输出可控**:stdout 截断到 16,000 字符(保头)、stderr 到 8,000 字符(保尾),再按整包 JSON 回缩到 `MAX_TOOL_CHARS`(25,000)以内,`notes` 提示「重定向到文件后用 `read` 分段读取」;exec 不会回 `tool.too_large`。
- 沙箱边界不变:`network=deny`(装依赖会失败,这是有意的)、`/workspace` 为工作目录、执行后 `touch` 续 TTL;审计行记 `tool_name/risk/decision/arguments/duration_ms/sandbox_id`。
- 设计见 `docs/design/tool-execution.md`(P1-L;修订了 `docs/design/tool-service.md` 的 D13)。

## 代码执行(run_code,P5)

- `run_code` 是 `risk=destructive`(legacy `danger`)、`executor=sandbox` 的函数工具:参数 `code`(1..20000 字符,Python 3 源码)、`cwd`(≤512 字符,相对 `/workspace`,绝对路径也必须落在其内)、`timeout_seconds`(1..120,默认 60)。
- **代码经 stdin 直传**:argv 固定 `[python3, -]`,源码走 `stdin`——不进 argv、不经 shell,多行脚本不必处理引号/转义(exec 的 `python3 -c` 与 heredoc 两条路都不再需要);`python3 -` 的语义:顶层语句直接执行、`__name__` 为 `"__main__"`、没有 `__file__`、traceback 文件名是 `<stdin>`、`input()` 立即读到 EOF。
- **默认关闭**(`TOOL__RUN_CODE_ENABLED=false`):关闭时目录不暴露、GET/execute 一律 404 `tool.not_found`。开启后 orchestrator 的 plan 闸与 confirm 闸都会因危险工具而中断,需人工放行。
- **与 `exec` 共用执行路径**:都由 `ToolRunner._run_process`(ensure → `SandboxProvider.exec` → 截断/回缩 → 审计)处理,区别只在 argv、`stdin` 与超时文案;结果信封、`notes`、非零退出码语义(仍为 `ok=true`)、截断上限(16,000/8,000,整包 ≤25,000)与 exec 一致。
- **沙箱边界不变**:`network=deny`、`/workspace` 为工作目录、执行后 `touch` 续 TTL;审计行记 `tool_name=run_code/risk/decision/arguments/duration_ms/sandbox_id`,代码全文不落库。
- 设计见 `docs/design/code-execution.md`(P5;与 `docs/design/tool-execution.md` 的 exec 单册互为姊妹)。

## 后台作业(job\_\*,P1-L M2)

- 三个工具都受 `TOOL__JOBS_ENABLED`(默认 false)控制:关闭时目录不暴露、GET/execute 一律 404 `tool.not_found`。
- `job_start`(`risk=destructive`、`executor=sandbox`):参数 `command`(1..4000)、`cwd`(≤512,必须落在 `/workspace` 内)、`name`(≤80,仅展示)。服务端把 command 经 `shlex.quote` 嵌入内层 `/bin/sh -lc` 脚本,再整体后台化(setsid 优先,nohup 兜底),立即返回 32 位 `job_id`;退出码写 `exit` 文件,pid 写 `pid` 文件。**先启动进程再落库**:落库失败会尽量 kill 刚启动的进程并回 `tool.upstream_error`。
- `job_output`(`risk=read`):参数 `job_id`、`tail_chars`(1000..20000,默认 8000)、`wait_seconds`(0..15,长轮询)。返回 `status`(running/exited/killed/timeout/lost)、`exit_code`、`stdout`/`stderr` 尾部、`finished_at`。非零退出码仍 `ok=true`(与 exec 一致)。
- `job_kill`(`risk=write`):SIGTERM 进程组 → 1s → SIGKILL;已结束的作业不报错,回 `already_finished=true` 与真实 `exit_code`。
- **惰性超时**:没有 sweeper,只有下一次 `job_output`/`job_kill` 才会发现运行超过 `job_max_runtime_seconds` 的作业并标记 `timeout` + 终止;长期无人查看的作业会占配额直到有人访问或沙箱被回收。
- **lost 语义**:沙箱 404/不可达或 pid 消失却无 `exit` 文件(microsandbox 进程被杀/OOM)都记 `lost`,不保证 at-least-once;日志只保留尾部,完整输出请用文件工具 `read` 日志目录 `/workspace/.tool-jobs/<job_id>/`。
- **并发与保留**:单账号 running 上限 `TOOL__JOB_MAX_CONCURRENT_PER_ACCOUNT`(超限 `tool.budget_exceeded`);启动新作业时 best-effort 清理超过 `TOOL__JOB_RETENTION_HOURS` 的作业目录与终态库行。多副本下 `job_kill` 靠条件 UPDATE 保证幂等。
- **审批边界**:`job_start` 是 destructive,`job_kill` 是 write——开启 `JOBS_ENABLED` 后 orchestrator 的 plan 闸/confirm 闸会因此中断,需人工放行。已知偏差:`job_kill` 在对话内仍是 write、走确认卡片(契约期望的「对话不可见」需 orchestrator 侧再加隐藏名单,本条为已知偏离)。

## 交互工具(P2)

- `ask_question` 已启用(`kind=interaction`、`executor=none`):本服务只做 schema 校验与字段归一(默认补 `allow_custom=true`、每题 `multi_select=false`),返回 `{"ok":true,"kind":"interaction","interaction":{"type":"question"},"normalized":{...}}`。
- 挂起/回答/恢复由 orchestrator 的 run 中断机制处理(见 `docs/design/tool-service.md` §7.7/§8.3);本服务不写 run 事件、不做审批决策。

## 受控写(P1)

- `write`/`edit` 的风险等级是 `write`:`approved=false` 的请求一律 403 `tool.confirmation_required`,只有 orchestrator 在两阶段确认/策略放行后才会带 `approved=true`。
- 原子写:同目录随机临时文件 + `fsync` + `os.replace`,不会出现半截文件;末段 symlink、多硬链接、`.env*` 与 `*.pem/*.key` 一律拒绝。
- 读前置:同 `run_id`/`scope_key` 内必须先 `read` 过目标路径(记录 sha256),否则 `tool.read_required`;读后文件变化 → `tool.stale_read`;`expected_sha256` 不匹配 → `tool.conflict`。
- 配额:单 run 写次数/写入字节/总调用次数超限 → `tool.budget_exceeded`。
- 幂等:`tool_call_id` 重放不重复落副作用,审计表 `tool_calls` 记录 `bytes_written`/`sha256_before`/`sha256_after`。

## 本地开发

```sh
npx nx run workspace-service:serve   # 沙箱来源(:8005)
npx nx run knowledge-service:serve   # 知识库检索来源(:8006,P2)
npx nx run tool-service:sync         # 依赖同步(禁用 npx nx sync,会被内置命令抢占)
npx nx run tool-service:serve        # :8008
npx nx lint tool-service
npx nx format tool-service
npx nx test tool-service             # 覆盖率阈值见 pyproject.toml
cd services/tool-service && uv run alembic upgrade head   # 或 AUTO_CREATE_TABLES=true
```

真沙箱(microsandbox)默认关闭:测试用 FakeSandboxProvider,未配置沙箱时执行器返回 `tool.upstream_unavailable` 而不是退回宿主机直读。

## 部署

本服务随仓库在腾讯云轻量服务器（首尔）上用 Docker Compose 部署；Vercel 只承载
`apps/web` / `apps/site` 静态层。编排见
[deploy/tencent/docker-compose.cvm.yml](../../deploy/tencent/docker-compose.cvm.yml)，
方案与验收见 [hybrid 部署方案](../../docs/design/hybrid-deployment.md) 与
[deploy/tencent/README.md](../../deploy/tencent/README.md)。

服务目录不再包含 Vercel 导出配置（`vercel.json` / `.python-version` /
`pyproject.toml` 的 `[tool.vercel]`），这些已于 2026-11-12 随部署主线切换清理。
