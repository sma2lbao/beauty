# agent-orchestrator

Agent 编排服务(阶段 A 骨架):会话与运行编排的控制面。架构与执行模型见 `docs/architecture.md` §6。

当前实现(阶段 A):

- run 垂直切片:`POST /api/v1/runs` 创建,`GET /runs/{id}` 查询,
  `GET /runs/{id}/events` 事件回放(§6.3 事件溯源读端),
  `GET /runs/{id}/artifact` 取交付物;
  `GET /runs/{id}/events/stream` SSE 事件流:重放 + 实时推送,`Last-Event-ID`
  断线续传,run 终态(completed/failed)自动 `stream.end` 收线;
- LangGraph 执行图(`agent_orchestrator/graph/`):plan → 审批闸(interrupt 人工确认)
  → execute → verify(返工 ≤3 轮)→ deliver;checkpointer 按 DATABASE_URL 自动选择:
  postgres → `langgraph-checkpoint-postgres`(图线程跨进程可恢复),sqlite/dev → InMemorySaver;
- 生命周期 API:`POST /runs/{id}/plan`、`PATCH /runs/{id}/plan`(审批前编辑计划)、
  `POST /runs/{id}/approve`、`POST /runs/{id}/reject`、`POST /runs/{id}/resume`
  (blocked → 重新进闸,保留已编辑计划,§6.2 goal 语义)、
  `POST /runs/{id}/retry`(failed → **另起一跑**并继承目标/验收标准/已批准计划,
  返回新 run;血缘见 `parent_run_id` 与 `run.retry.requested.v1`)、
  `POST /runs/{id}/confirm`(写操作放行/拒绝,§6.2)、
  `POST /runs/{id}/instructions` + `GET /runs/{id}/instructions`(追加指令,§6.5);
- 对话垂直切片(§6.7):`POST /api/v1/conversations` 建会话、`GET /conversations`(最近活跃在前)、
  `GET /conversations/{id}`(附进行中的 `active_run_id`,前端据此接 SSE)、
  `GET /conversations/{id}/messages` 消息回放、`POST /conversations/{id}/messages` 发消息、
  `POST /conversations/{id}/messages/stream` **SSE 流式**发消息
  (`meta` → `delta`\* → `done`,失败以 `error` 帧收尾;直接对话的正文边产出边下发);
  发消息**先分流**(`services/triage.py`):`mode=auto|chat|task`,auto 由模型判别,chat 直接答一条
  不建 run(`services/chat.py`),task 才建 run 并立即规划或并入当前 run 的追加指令;
  判别失败或无模型时退回 task(确定性桩仍产出计划),显式 chat 而无模型返回 409 `chat.llm_disabled`;
- 状态守卫:非法迁移 409(`run.invalid_state`),统一错误模型来自 `common`;
- 失败与重试(§6.2):失败即终态(`failed` + `run.failed.v1`,原因留
  `status_reason`),**不原地重放**——失败点的检查点不可信,且旧 run 的历史是
  审计凭据,不能被"重试"改写。重试派生新 run 继承已批准计划(计划节点检测到
  已有计划会短路,所以只重走审批闸、不重复调用模型);重试后仍需人工放行,
  已产生的副作用(如已发邮件)不会被自动撤销;
- `/healthz` `/readyz`:存活/就绪探针;
- schema 管理:alembic(async env,DB URL 与运行服务同源,见 `migrations/env.py`);
  `uv run alembic upgrade head` / `uv run alembic downgrade base`,命名约定保证回滚无损;
  **dev 直建库(测试/开发期的 `create_all`)没有 alembic 版本记录,接入迁移前先对齐基线**:
  `uv run alembic stamp <该库结构对应的 revision>` 再 `upgrade head`,否则会从头建表并撞上
  "table already exists";另外 `create_all` 只建缺失的表,**不会给既有表加列**——
  代码里新增列后必须跑迁移,服务默认 `AUTO_CREATE_TABLES=false`,不迁移就会 500;
- 生命周期含 failed 终态:图执行(plan/execute)异常在调用边界统一收敛为
  `run.failed.v1` 事件 + 状态落库,不向调用方裸奔(§6.2);
- LLM 接入(架构 §5 唯一出口):`LLM_ENABLED=true` 时 plan/execute/verify 经
  `llm-gateway` 生成计划、执行结果与逐条验收判定(节点工厂注入,见 `graph/nodes.py`);
  false 或网关未起时回退确定性桩。测试注入 FakeLLM,零网络零 token。
- 返工循环(§6.1):验收未过 → 回 execute,带验收小结继续返工;≤3 轮;
  耗尽仍不过 → `fail_node` 诚实终结 failed(不伪装 completed);
- 工具调用(架构 §8):`TOOLS_ENABLED=true` 时 execute 走 function-calling——
  模型请求工具 → 工具注册表(`services/tools.py`)执行 → 结果回喂模型,≤3 轮;
  **能力按提供方分组,未开启的不暴露**(能力即权限边界):
  - 连接器:`list_connections`/`fetch_emails`(只读)、`send_email`(写操作);
  - 本地文档库(`DOCUMENTS_ENABLED`):`list_documents`/`read_document`
    (受限目录只读,后缀白名单、越界/绝对路径拒绝、超限拒读);
  - 日历(`CALENDAR_ENABLED`,ICS):`list_calendar_events`(不展开 RRULE);
  - 企业知识库(`KNOWLEDGE_ENABLED`):`search_knowledge`——经 knowledge-service
    向量检索,让模型"先查资料再回答"(`docs/architecture.md` §9);
    **工具执行失败不炸图**:错误文本回喂让模型决策,每次调用落
    `run.tool.called.v1` 事件(name/arguments/ok/detail),SSE 实时可见;
- 写操作确认闸(§6.2):写工具(requires_confirmation)**绝不直接执行**——
  execute 收集后交 `confirm_gate` 中断,run 转 `awaiting_confirmation` 并落
  `run.confirmation.required.v1`;`POST /runs/{id}/confirm` 放行则由
  `tool_exec` 执行并回喂,拒绝则跳过并把原因记入 results;确认 ≤3 轮防死循环。

- 鉴权(架构 §10):默认开启——校验 `Authorization: Bearer <IdP access token>`
  (OIDC 发现 + JWKS,RS256;自建 IdP 即 account-service :8001),租户取自
  token claim(默认 `tenant_id`)——**开启时伪造 `X-Tenant-Id` 无效**;显式
  关闭鉴权(仅特殊部署)才沿用临时头,缺失即 401 `auth.header_missing`;
- 服务间调用 token(§5):本服务对下游(connector/knowledge/workspace)的内部
  调用**一律**经 `TokenBroker`(`services/token_broker.py`)用共享内部密钥
  (`INTERNAL_AUTH_SECRET`,配置 `AGENT_ORCHESTRATOR__INTERNAL_AUTH_SECRET`,
  与 account 的 `ACCOUNT__INTERNAL_SECRET` 一致)向 account 的
  `POST /v1/auth/issue` 换"以目标租户为 claim"的标准 token,进程内按租户
  缓存到过期前 60s——**与用户面走同一个 JwtVerifier,不设后门**;不再有
  `X-Tenant-Id` 裸调路径,密钥未配置时在换发处即报错;阶段 B 换 mTLS/OBO 时
  只换 TokenBroker 实现,三个下游客户端的注入点不变;

- 追加指令(§6.5):`POST /runs/{id}/instructions` 排队(终态 run 追加 409),
  在**下一个人工边界**消费——规划(plan/resume)、放行(approve)、
  写操作确认(confirm);消费即标记 `consumed` 并落
  `run.instruction.queued.v1` / `run.instruction.consumed.v1`;内容经
  `extra_instructions` 并入 plan/execute/verify/deliver 的提示词(模型必须遵循)。

- 交付物(§7):deliver 由 LLM 合成最终成品,落 `run_artifacts`(每 run 一件);
  取件 `GET /runs/{id}/artifact`,事件流记 `run.delivered.v1`
  (artifact_id/kind/title/chars/size_bytes/storage);
  未交付取件 404 `artifact.not_found`,failed 终态不产交付物;
- 交付物存储后端(§7 阶段 B):`ARTIFACT_STORE=database`(默认,正文存 DB 列)或
  `filesystem`(正文落 `ARTIFACT_ROOT`,行里只留 `object_key`/`size_bytes`;
  键按 `<租户>/<run>/<交付物>.md` 分层,拒绝目录穿越);对象丢失 → 500
  `artifact.object_missing`。换 S3 兼容存储只需实现 `common.artifacts.ArtifactStore`;
- outbox 运维面:`GET /outbox/events`(可按 `status` 过滤)、`GET /outbox/stats`、
  `POST /outbox/events/{id}/retry`、`POST /outbox/retry-failed`——失败事件可见、
  可人工重放(重放回 pending 并清零尝试数;下次投递成功才清 `last_error`);
  端点**租户隔离**(平台级运维需要 admin scope,阶段 A 不做);未启用 outbox 时
  返回 409 `outbox.disabled`,而不是空列表(免得把"没开"误读成"没事件");控制台页 `/outbox`;
- 事务性 outbox(`OUTBOX_ENABLED=true`):run 事件与业务状态**同一事务**写入
  `outbox_events`,再由后台 dispatcher 至少一次投递(失败退避、超限落 `failed`
  留痕);`KNOWLEDGE_INGEST_ENABLED=true` 时挂上首个真实消费者
  **交付物自动入库**:`run.delivered.v1` → knowledge-service(§8 摄取),
  之后 agent 的 `search_knowledge` 就能查到刚交付的成品;
- 交付物镜像(`WORKSPACE_ENABLED=true`):交付时**同时**推给 workspace-service
  (§7 里 artifacts 归属 workspace);镜像成败如实记进 `run.delivered.v1` 事件
  (`workspace_artifact_id` / `workspace_error`)——镜像失败不丢交付物。
- 沙箱客户端(P1,仅基础设施):`services/sandbox.py` 封装
  workspace-service 内部 `/v1/sandboxes`(创建/查询/执行/文件读写/续期/销毁),
  服务间 Bearer 来自 TokenBroker;`SANDBOX_ENABLED=true` 才装配,默认关闭。
- **stdio MCP(P2)**:`MCP_CLIENT` 注册表新增 `transport=stdio`——命令在沙箱内
  拉起(`services/mcp_stdio.py` 的 `StdioSandboxPool`),经 stdin/stdout JSON-RPC
  执行 `tools/list`/`tools/call`;probe 只用标准库,按 `runtime=python|node`
  写 `/workspace/.mcp/probe.py|.js`。需要 `MCP_STDIO_ENABLED=true` **且**
  `SANDBOX_ENABLED=true`(否则 test 端点明确报 "需要启用沙箱",tool_specs 跳过
  stdio server)。沙箱按**对话级**复用(`ToolContext.conversation_id`;无对话退化
  到 run),进程内缓存;命令必须命中 `MCP_STDIO_COMMAND_ALLOWLIST`
  (`npx/uvx/node/python/python3/bunx`),参数不经 shell;**env 与 token 一样
  只存密文,读端只回 `has_env/env_keys`**。P2 沙箱出网为 `public`(允许
  npx/uvx 安装),P4 再切 allowlist。

后续补齐:更多工具与技能、S3 兼容交付物存储、Redis Streams 直播(替代 SSE 轮询)。

命令(仓库根执行):`npx nx run agent-orchestrator:serve`(:8002)/ `npx nx test agent-orchestrator` / `npx nx lint agent-orchestrator`
