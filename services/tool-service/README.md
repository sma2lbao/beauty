# tool-service(工具服务, :8008)

内置工具的统一执行入口:模型侧只看到裸名工具(`read`/`write`/`edit`/`glob`/`grep`/`list_dir`/`ask_question`),执行、限额、审计、幂等与租户隔离都由本服务负责,orchestrator 通过 HTTP 客户端调用(设计见 `docs/design/tool-service.md`)。

## 职责边界

| 关注点 | 归属 |
| --- | --- |
| 工具目录(catalog)、参数 schema、限额 | tool-service(代码即真源,`tool_service/services/catalog.py`) |
| 工具执行(沙箱内 helper 脚本) | tool-service → workspace-service 沙箱 |
| 审批决策(allow/ask/deny) | orchestrator 保留;tool-service 只做风险下限防御 |
| run 事件流(`run.tool.called.v1`)与 SSE | orchestrator 唯一落库;本服务不写 run_events |
| 调用审计与幂等(`tool_calls` 表) | tool-service |
| `ask_question` 的挂起/恢复 | orchestrator 图(`interrupt`)+ run 事件 |

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/v1/tools` | 工具清单(默认只返回已启用;kind/executor 过滤) |
| GET | `/v1/tools/{name}` | 单个工具描述;未知或未启用 → 404 `tool.not_found` |
| POST | `/v1/tools/{name}:execute` | 执行;业务错误一律 HTTP 200 + 失败信封 |
| GET | `/v1/tool-calls` | 调用审计分页(run_id/tool_name/ok 过滤) |
| GET | `/v1/tool-calls/{tool_call_id}` | 单条审计(跨租户 404) |
| GET | `/healthz` `/readyz` | 存活/就绪(就绪探针查 DB) |

幂等:`tool_call_id` 在租户内唯一;同 id 同参数重复调用返回 `idempotent_replay=true` 的重放结果,参数不同 → 409 `tool.idempotency_conflict`。

## 配置(`TOOL__` 前缀)

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `TOOL__DATABASE_URL` | `sqlite+aiosqlite:///./tool.db` | 审计库(生产 postgresql+asyncpg) |
| `TOOL__AUTO_CREATE_TABLES` | `false` | 生产 false,迁移走 alembic |
| `TOOL__AUTH_ENABLED` | `true` | 校验租户 JWT(内部签发走 account-service) |
| `TOOL__AUTH_ISSUER` | `http://127.0.0.1:8001` | 签发方/JWKS 来源 |
| `TOOL__INTERNAL_SECRET` | 空 | 服务间换取租户 token 的 internal secret |
| `TOOL__WORKSPACE_SERVICE_URL` | `http://127.0.0.1:8005` | 沙箱来源 |
| `TOOL__WORKSPACE_TIMEOUT_SECONDS` | `60` | 沙箱 HTTP 超时 |
| `TOOL__TOOL_TIMEOUT_SECONDS` | `30` | 单次工具执行默认上限 |
| `TOOL__SANDBOX_TOOL_TTL_SECONDS` | `1800` | 沙箱滑动 TTL |
| `TOOL__READ_BEFORE_WRITE_ENABLED` | `true` | 写前读校验:同 run 内没 read 过同路径 → `tool.read_required` |
| `TOOL__MAX_WRITES_PER_RUN` | `50` | 单 run 写操作次数上限(P1) |
| `TOOL__MAX_WRITE_BYTES_PER_RUN` | `5242880` | 单 run 写入字节上限(P1) |
| `TOOL__MAX_TOOL_CALLS_PER_RUN` | `60` | 单 run 工具调用总次数上限(P1) |

## 受控写(P1)

- `write`/`edit` 的风险等级是 `write`:`approved=false` 的请求一律 403 `tool.confirmation_required`,只有 orchestrator 在两阶段确认/策略放行后才会带 `approved=true`。
- 原子写:同目录随机临时文件 + `fsync` + `os.replace`,不会出现半截文件;末段 symlink、多硬链接、`.env*` 与 `*.pem/*.key` 一律拒绝。
- 读前置:同 `run_id`/`scope_key` 内必须先 `read` 过目标路径(记录 sha256),否则 `tool.read_required`;读后文件变化 → `tool.stale_read`;`expected_sha256` 不匹配 → `tool.conflict`。
- 配额:单 run 写次数/写入字节/总调用次数超限 → `tool.budget_exceeded`。
- 幂等:`tool_call_id` 重放不重复落副作用,审计表 `tool_calls` 记录 `bytes_written`/`sha256_before`/`sha256_after`。

## 本地开发

```sh
npx nx run workspace-service:serve   # 沙箱来源(:8005)
npx nx run tool-service:sync         # 依赖同步(禁用 npx nx sync,会被内置命令抢占)
npx nx run tool-service:serve        # :8008
npx nx lint tool-service
npx nx format tool-service
npx nx test tool-service             # 覆盖率阈值见 pyproject.toml
cd services/tool-service && uv run alembic upgrade head   # 或 AUTO_CREATE_TABLES=true
```

真沙箱(microsandbox)默认关闭:测试用 FakeSandboxProvider,未配置沙箱时执行器返回 `tool.upstream_unavailable` 而不是退回宿主机直读。
