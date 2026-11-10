# 应用级 hooks（P1-B）

> 状态：P1-B 实施契约（2026-11-10）。范围是**账号自定义的五个生命周期挂点**：
> PreToolUse / PostToolUse / SessionStart / SessionEnd / PreCompact。
> 与 [tool-policy.md](tool-policy.md) 共用同一个工具决策点（P1-A 先落，本文件在其后实施）；
> 工具执行面见 [tool-execution.md](tool-execution.md)，压缩见 [context-engineering.md](context-engineering.md)，
> 出站安全复用 [mcp-security.md](mcp-security.md) 的 net_guard。

## 1. 目标与非目标

当前 agent 循环里没有任何“外部可插拔”的扩展点：审批策略只能改工具的动作，无法在工具调用前后
观察或拒绝；会话创建/删除与上下文压缩也没有回调。P1-B 补一层**应用级 hooks**：

1. **五个事件**：PreToolUse、PostToolUse、SessionStart、SessionEnd、PreCompact；
2. **可拦截**：PreToolUse 能拒绝本次工具调用；PreCompact 能跳过本次压缩；
3. **可观察**：PostToolUse 可给模型回喂一段 note；SessionStart/End 只观察；
4. **出站 HTTP（webhook）**：hook = 账号配置的 HTTPS 地址 + 签名密钥，服务端 POST JSON；
5. **账号自管**：hook 属账号私有，随账号删除级联清理；API 增删改查，前端设置页管理；
6. **安全默认关闭**：所有 hook 受部署开关 HOOKS_ENABLED 控制（默认关），关闭时 API 一律 409。

为什么 MVP 选 webhook 而不是 shell：shell hook 等于在 orchestrator 进程内执行任意代码，
多租户共享部署不可接受；webhook 只做出站、可复用 net_guard 防 SSRF、可签名、可审计。
shell handler 留到有沙箱执行面之后（见 §12）。

非目标（本批不做）：

- shell 命令型 hook（需要 workspace-service 沙箱，二期）；
- 声明式 hook DSL；异步队列/重试（MVP 同步，超时短路 fail-open）；
- hook 市场、跨账号共享、hook 级 RBAC；
- 修改工具参数（PreToolUse 只能拒绝，不能改写 arguments——改写留给策略引擎）；
- 改写工具结果正文（PostToolUse 只追加模型可见 note，不改审计与投影）。

## 2. 事件与语义

| 事件         | 挂点                                                                                                                      | 能否阻断   | 输出用途                    | 失败语义                            |
| ------------ | ------------------------------------------------------------------------------------------------------------------------- | ---------- | --------------------------- | ----------------------------------- |
| PreToolUse   | 四个工具执行点：execute.py 自动分支、tool_exec.py 人工确认重放、chat.py 对话 READ、tool_confirmations.py 对话写确认后执行 | 能（deny） | 拒绝理由回喂模型            | 超时/异常/非 2xx 默认放行 + warning |
| PostToolUse  | 同上四处执行完、回喂模型前                                                                                                | 不能       | note 追加到模型可见输出尾部 | 忽略 + warning                      |
| SessionStart | services/conversations.py 创建会话提交后                                                                                  | 不能       | 仅记录                      | 忽略 + warning                      |
| SessionEnd   | services/conversations_queries.py 删除会话提交后（post_commit）                                                           | 不能       | 仅记录                      | 忽略 + warning                      |
| PreCompact   | services/compaction.py 阈值判定通过后、summarize 前（任务路径同）                                                         | 能（skip） | 跳过本次压缩                | 超时/异常默认继续压缩 + warning     |

**顺序**：PreToolUse hook → 审批策略（P1-A）→ 工具执行 → PostToolUse hook → 回喂模型。
PreToolUse 在 approval.decide **之前**求值（deny 短路，不产生 pending/确认；allow 才进入策略），
因此对话写工具的确认流程不会出现“先问用户再被 hook 拒绝”。
hook 的 allow **不能**绕过审批策略（审批仍是权威）：hook 只有“否决”和“观察”两种权力，
避免两套策略互相放大权限。人工确认后的 tool_exec 重放与对话确认后的 tool_confirmations.confirm
执行同样先过 PreToolUse（hook 是独立否决权）。

## 3. 数据模型

新表 account_hooks（UUIDMixin + AccountMixin + TimestampMixin）：

| 列              | 类型         | 约束 / 默认               | 说明                                          |
| --------------- | ------------ | ------------------------- | --------------------------------------------- |
| event           | String(32)   | not null, index           | 五个事件之一（API 校验枚举）                  |
| matcher         | String(200)  | not null, default "\*"    | 工具名精确或 glob（仅 Pre/PostToolUse 生效）  |
| kind            | String(16)   | not null, default webhook | 本批只有 webhook（shell 预留）                |
| target          | String(1024) | not null                  | HTTPS URL（创建时与每次请求前都过 net_guard） |
| secret          | String(128)  | not null                  | HMAC 签名密钥；只在创建响应里返回一次         |
| timeout_seconds | Integer      | not null, default 3       | 实际超时取 min(本列, HOOKS_TIMEOUT_SECONDS)   |
| enabled         | Boolean      | not null, default true    | 临时关闭不删记录                              |

索引：(account_id, event)；唯一约束不加（同事件允许多条，按 created_at, id 升序串行）。
账号删除由 AccountMixin 级联；单账号最多 20 条（超出 409 hooks.too_many）。

## 4. 协议

### 4.1 请求

`POST <target>`，`Content-Type: application/json`，请求体：

```json
{
  "event": "PreToolUse",
  "account_id": "…",
  "conversation_id": "…",
  "run_id": "…",
  "tool": "write_file",
  "arguments": "{\"path\":\"a.txt\"}",
  "result": "…",
  "tokens_before": 0,
  "reason": "…"
}
```

arguments / result 各截断到 8000 字符（尾部标注 …(truncated)）；与事件无关的字段省略。
请求头：`X-DSH-Hook-Event`、`X-DSH-Hook-Id`、`X-DSH-Timestamp`（unix 秒）、
`X-DSH-Signature: sha256=<hex(hmac_sha256(secret, timestamp + "." + body))>`。

### 4.2 响应

HTTP 2xx 且响应体最后一行 JSON 形如：

```json
{
  "decision": "allow|deny|skip",
  "reason": "人类可读理由",
  "note": "给模型的补充说明"
}
```

- 空体、非 JSON、未知 decision → 视为 allow；
- decision=deny 仅 PreToolUse 有效；skip 仅 PreCompact 有效，其余事件忽略；
- reason 截断 512 字符、note 截断 1000 字符，回喂前统一清洗（去控制字符）；
- 多个命中 hook 串行执行，**首个 deny/skip 短路**，其余不执行；
- 非 2xx、连接失败、超时：默认放行 + warning（HOOKS_FAIL_CLOSED=true 时改为 deny）。

### 4.3 网络与资源

- 出站用 `httpx.AsyncClient`（复用 MCP client 的 transport 注入方式），整体 deadline 由 timeout 控制；
- 目标 URL 在创建与每次请求前都过 `services/net_guard.assert_url_allowed`（拒绝私网/环回/元数据地址，
  白名单见 MCP_ALLOWED_HOSTS）；只允许 https（HOOKS_ALLOW_HTTP 默认 false，仅本地调试）；
- 响应体上限 16 KiB，超出丢弃并 warning；并发度每事件最多 5 条串行（避免放大延迟）。

## 5. 端口与状态快照

graph/ports.py 增：

```python
class HookDecision(StrEnum):
    ALLOW = "allow"
    DENY = "deny"
    SKIP = "skip"

@dataclass(frozen=True, slots=True)
class HookOutcome:
    decision: HookDecision = HookDecision.ALLOW
    reason: str = ""
    note: str = ""

class HookPort(Protocol):
    async def run(
        self,
        hooks: Sequence[Mapping[str, Any]],
        event: str,
        payload: Mapping[str, Any],
    ) -> HookOutcome: ...
```

- `hooks` 是**账号 hook 快照**（`load_hooks` 读出并过滤 enabled），由 run 创建时加载进
  `RunGraphState.hooks`（与 approval_policies 同生命周期），对话路径由调用方加载后传入；
  图节点不查库（依赖方向不变）。
- `GraphPorts` 增 `hooks: HookPort = _NullHooks()`（默认全放行），生产装配
  `services/run_execution_approvals.py::_graph_ports()` 注入 `services/hooks.WebhookHookRunner`；
- SessionStart/End/PreCompact 不进图，由 services 直接调 `HookRunner.run(...)`；
- 调试用 `NullHooks`，保证现有 1029 个测试零改动（GraphPorts 新字段有默认值）。

## 6. 各挂点实现要点

### 6.1 PreToolUse（四个执行点）

在 `approval.decide` **之前**、`toolkit.execute(...)` 之前插入（tool_exec 与 tool_confirmations
没有 decide，直接在执行前插入）：

1. 取 event=PreToolUse 且 matcher 命中的 hook 快照；
2. `deny` → 图内回喂 tool_error（`hook denied: {reason}`）、不调用 execute，
   审计记录 decision=hook_denied（沿用 `_record` 的 decision 字段）；chat 路径不落 pending 确认；
   对话确认端点返回 `409 tool.hook_denied` 且不改变 confirmation 状态（用户可取消）；
3. `allow` → 原逻辑（策略 auto、人工确认重放、对话写确认后放行都不变）。

四个执行点：`execute.py`（run 自动分支）、`tool_exec.py`（run 人工确认重放）、
`chat.py::_execute_calls`（对话 READ）、`tool_confirmations.confirm`（对话写确认后执行）。
`services/mcp_server.py` 的 `toolkit.execute` 是 orchestrator 作为 MCP server 被外部调用，
没有 agent 主链路上下文，**显式不挂 hook**。

### 6.2 PostToolUse

四处在拿到 output 之后、构造 tool 消息之前调用；`note` 非空时只追加到**模型可见**内容：
`output + "\n\n[hook note] " + note`，且必须在 offload 判定与 `_record` 之后（不改变审计字段与
事件投影）。hook 失败不影响工具成败与 audit 的 ok。

### 6.3 SessionStart / SessionEnd

- SessionStart：`create_conversation` 提交后 best-effort（post_commit 或直接 await + try/except，
  不阻塞返回，异常只 warning）；
- SessionEnd：`delete_conversation` 提交后（post_commit，fire-and-forget），失败不回滚删除；
- 上下文：account_id、conversation_id，tool/arguments/result 为空。

### 6.4 PreCompact

`compaction.py::compact_history` 在阈值判定通过后、`summarize` 之前执行；`skip` → 直接返回 None
（调用方保持原历史），记 warning `compaction.skipped_by_hook`；任务路径
`conversations_context.py::_task_compaction` 同语义。不新增 run.compacted.v1 变体
（事件分类学稳定，见 [event-taxonomy.md](event-taxonomy.md)）。

## 7. API

账号鉴权（Bearer + account 隔离），前缀 `/api/v1/hooks`：

| 方法   | 路径                      | 说明                                                                       |
| ------ | ------------------------- | -------------------------------------------------------------------------- |
| GET    | /api/v1/hooks             | 列表，可选 event 过滤，按 created_at, id 升序；secret 不回显               |
| POST   | /api/v1/hooks             | 创建；校验事件枚举、https、matcher 长度、超时 1..30；响应一次性返回 secret |
| PATCH  | /api/v1/hooks/{id}        | 局部更新（matcher/target/timeout/enabled）                                 |
| DELETE | /api/v1/hooks/{id}        | 删除，warning 级日志（不入 audit_logs）                                    |
| POST   | /api/v1/hooks/{id}/rotate | 轮换 secret，响应返回新 secret                                             |

错误码：hooks.disabled（总开关关）、hooks.too_many（>20 条）、hooks.not_found（404，账号隔离）、
hooks.invalid_target（非 https / 私网地址 / 解析失败）。

配置（`services/agent-orchestrator/agent_orchestrator/core/config.py`）：

- `hooks_enabled: bool = False`（总开关，关时 API 全部 409、执行器空跑）；
- `hooks_timeout_seconds: float = 3.0`（上限 30，取 min）；
- `hooks_fail_closed: bool = False`、`hooks_max_per_account: int = 20`、
  `hooks_allow_http: bool = False`（仅本地调试）。

## 8. 前端契约

新增 `apps/web/src/lib/hooks-api.ts`（list/create/update/remove/rotate，类型 `Hook`/`HookInput`；
创建与轮换响应含一次性 `secret`）与 `features/settings/hooks-panel.tsx` + `use-hooks-model.ts`：

- 设置页 `AccountCard` 之后新增“自动化 hooks”卡：表格（事件/匹配/target 摘要/超时/开关/删除）、
  新建表单（事件下拉、matcher、HTTPS URL、超时）；
- 创建/轮换成功后弹一次性 secret（可复制，关闭后不可再取），并提示“接收端需校验 X-DSH-Signature”；
- 总开关关闭或后端 409 时展示说明（“当前部署未启用 hooks”），不静默失败；
- 源文件 ≤400 行。

## 9. 安全边界与失败语义

- **出站 SSRF**：创建与请求前都过 net_guard；私网/环回/元数据地址 422/拒绝执行；
  hook 用**严格模式**：创建时额外显式解析 DNS，解析失败即拒绝（与 MCP 的“解析失败放行”
  不同，hook 是账号主动配置的固定目标）；请求时仍走 assert_url_allowed 兜底；
- **签名**：secret 只在创建/轮换响应出现一次，DB 明文存储（与 MCP 凭证密文模式的差距记为残余）；
  接收端按 timestamp+body HMAC 校验，服务端不做重放窗口（由接收端负责）；
- **不传密钥**：payload 不含 token/API key/连接器凭据；arguments/result 截断且只读；
- **fail-open 默认**：观察类事件与超时一律不阻塞主流程；HOOKS_FAIL_CLOSED=true 才收紧；
- **输出清洗**：reason/note 去控制字符、截断；绝不把响应体原样回喂模型（只取 JSON 字段）；
- **PreToolUse 不能放行**：deny 之外一律回到审批策略，避免 hook 绕过 P1-A 的 deny 规则；
- **性能**：hook 在关键路径同步 await（PreToolUse/PreCompact），单条超时 + 串行短路限制放大；
  PostToolUse 与 Session 类同样 await，但失败吞掉（不影响主流程）。

## 10. 测试清单

- `services/hooks.py`：payload 构造/截断、签名计算、非 2xx/超时/连接失败降级、deny/skip 短路、
  note 清洗与截断、fail_closed 语义、总开关关闭空跑、net_guard 拒绝私网；
- `graph/ports.py`：`NullHooks` 放行；`GraphPorts` 新字段默认值兼容既有构造（1029 测试零改动）；
- `execute.py`：hook deny 回 tool_error 且不调用 execute/不触发审批副作用；allow 后审批仍生效；
- `tool_exec.py`：人工确认后重放前 deny → 不执行、记 hook_denied；
- `chat.py`：hook deny 不落 pending；PostToolUse note 进模型可见内容；
- `tool_confirmations.confirm`：deny → 409 `tool.hook_denied` 且不执行、confirmation 保持 pending；
  放行后 PostToolUse note 写入持久化 output；
- `compaction.py`/`conversations_context.py`：skip 跳过压缩、异常继续压缩；
- `conversations.py`/`conversations_queries.py`：start/end 调用与异常吞掉、end 在提交后；
- API：CRUD/rotate、事件枚举 422、https 与私网 422、命令开关 409、上限 409、账号隔离 404；
- 迁移：upgrade/downgrade（sqlite 临时库）+ `alembic check`。

## 11. 文件清单（2026-11-10 后端已落地）

后端（已提交 13ec46d）：`models/hook.py`、`repositories/hook.py`、`schemas/hook.py`、
`services/hooks.py`、`api/v1/hooks.py`、`api/v1/router.py`、`graph/ports.py`、`graph/state.py`、
`graph/nodes/audit.py`、`graph/nodes/execute.py`、`graph/nodes/tool_exec.py`、`services/chat.py`、
`services/tool_confirmations.py`、`services/compaction.py`、`services/conversations.py`、
`services/conversations_queries.py`、`services/conversations_messages.py`、
`services/run_execution_approvals.py`、`services/run_execution_invoke.py`、`core/config.py`、
`core/container.py`、`core/container_tools.py`、迁移 `b7f3d1c8e4a2`（`down_revision` = P1-A `e2b7c4a9d1f3`）、
`tests/test_hooks_service.py`、`tests/test_hooks_api.py`、`tests/test_hooks_mounts.py`。
（PreCompact 只挂在 `compaction.py::compact_history`，任务路径 `_task_compaction` 复用它，无需另挂。）
前端（Web 已落地）：`lib/hooks-api.ts`、`features/settings/hooks-panel.tsx`、
`features/settings/hooks-panel.test.tsx`、`use-hooks-model.ts`、`hook-secret-dialog.tsx`。

## 12. 残余与后续

- shell 命令型 hook 未做（需沙箱执行面，workspace-service 沙箱或 tool-service exec），二期；
- 无异步执行/重试/排队，hook 慢直接拖慢工具调用（超时兜底）；
- secret DB 明文存储（应复用 BYOK/MCP 的 envelope 加密），轮换需人工；
- 无声明式 DSL、无参数改写、matcher 只有精确/glob；
- hook 不写 audit_logs（量大且非管理动作），只有结构化日志与 warning；
- 已按计划串行落地：P1-A 先合并，本契约的迁移接在 P1-A revision `e2b7c4a9d1f3` 之后
  （`b7f3d1c8e4a2`）。
