# agent-orchestrator

Agent 编排服务:会话与运行编排的控制面(工具面/审批/事件流/沙箱/技能包/公开 MCP 均已运行)。架构与执行模型见 `docs/architecture.md` §6。

当前实现:

- run 垂直切片:`POST /api/v1/runs` 创建,`GET /runs/{id}` 查询,
  `GET /runs/{id}/events` 事件回放(§6.3 事件溯源读端),
  `GET /runs/{id}/steps` 节点步骤投影(S1:每个图节点一条 `run.step.v1` 的
  只读升序投影;该事件不进事件流),
  `GET /runs/{id}/items` 过程项投影(P1-D:`run.item.v1` 按 `item_id` 合并相位;
  该事件**进**事件流与 SSE),
  `GET /runs/{id}/artifact` 取交付物;
  `GET /runs/{id}/events/stream` SSE 事件流:重放 + 实时推送,`Last-Event-ID`
  断线续传,run 终态(completed/failed/cancelled)自动 `stream.end` 收线;
- **正文增量流**(`graph/delta.py` + `services/runs.py`):plan/execute/verify/deliver
  四个阶段的模型正文以 `token.delta.v1` 事件落 `run_events`(payload `{text, phase}`,
  text 是**增量**片段,按 seq 拼接即全文),执行期前端订阅 run 事件流即可看到正文
  边产出边显示——任务路径的会话 SSE 不重复下发正文,只给 `meta`/`done`。攒批落库
  (`RUN_DELTA_FLUSH_CHARS=240` / `RUN_DELTA_FLUSH_SECONDS=0.25`,阶段切换一定切批),
  增量事件**不进 outbox**(属于实时展示,不是跨服务事实);开关
  `RUN_DELTA_STREAMING=false` 可关回"每阶段只在终态落一条";节点仍是纯函数:
  只把文本推给 contextvar 绑定的 sink,落库发生在 services 层的图调用边界;
- LangGraph 执行图(`agent_orchestrator/graph/`):plan → 审批闸(按需 interrupt)
  → execute → verify(返工 ≤3 轮)→ deliver;审批闸按**装配工具的风险**自动开关:
  工具里没有 DANGER 级(未信任 MCP、对外不可逆操作)时计划生成后**自动放行**
  (`run.planned.v1` 带 `auto_approved=true`,并补一条 `run.approved.v1`),
  `AGENT_ORCHESTRATOR__PLAN_AUTO_APPROVE=false` 可关回"每跑必审";有 DANGER
  工具才 interrupt 人工放行。checkpointer 按 DATABASE_URL 自动选择:
  postgres → `langgraph-checkpoint-postgres`(图线程跨进程可恢复),sqlite/dev → InMemorySaver;
- **节点步骤投影(S1,2026-10-25)**:plan/execute/tool_exec/verify/deliver 每个节点
  执行完落一条 `run.step.v1`(payload `step_id`/`node`/`phase`/`attempt`/`round`/
  `status`/`started_at`/`finished_at`/`duration_ms`,`record_outbox=False`),
  `step_id = {run_id}:{attempt}:{node}:{round}` 是可寻址身份;`GET /runs/{id}/steps`
  按 seq 升序返回投影,前端「执行步骤」时间线读它。该事件**不进** `GET /events`
  与 SSE(避免淹没用户事件流,所以可见事件 seq 有空洞,只能当单调游标);采集在
  一次图调用边界完成,执行中的节点要等该次 `ainvoke` 返回后才出现(未接 astream);
  usage 的 step 维度(S2)与 step↔checkpoint/rewind(S3)未做,见
  `docs/gap-2026-10/07-run-step-model.md`;
- **过程项分类学(P1-D,2026-10-28)**:`run.item.v1` 把 run 内原子过程项统一成
  `kind`(plan/tool/file_change/mcp_call/search/todo)× `phase`(started/delta/
  completed/failed),`item_id = {run_id}:{attempt}:{kind}:{n}`,plan 版本化用
  `{run_id}:plan:{n}`(编辑计划落新版本并 `supersedes` 上一版);payload 带
  `thread_id`(有会话用会话 id,否则沿 `parent_run_id` 上溯到根 `run:<root>`)、
  `turn_id = run.id`、`step_id`(挂到当前 `run.step.v1` 步骤);事件 append-only、
  `record_outbox=False` 但**对用户可见**(进 `GET /runs/{id}/events` 与 SSE 回放);
  工具项由图节点经 `runtime.items` 上报、`_item_stream` 在图调用边界落库,plan 由
  `_invoke_plan`/`update_plan` 直接落,todo 由 `todo_write` 快照落;契约见
  `docs/design/event-taxonomy.md`;
- **子代理委派(P1-E,2026-10-28)**:任务执行的注入工具多一个 `spawn_subagent`
  (`services/subagents.py`,`WRITE` 级、不进确认闸),让主 run 把一段子任务派给
  **子 run** 独立跑完再取摘要——子 run 是真正的 run(自己的事件流/交付物/用量),
  父 run 只多一条过程项:
  - 暴露条件:在 run 上下文里、当前 run **不是子代理**(`parent_kind != "subagent"`)、
    `SUBAGENT_ENABLED=true`(默认 true);深度/数量上限
    `SUBAGENT_MAX_DEPTH=1` / `SUBAGENT_MAX_PER_RUN=3`;
  - 入参 `title`(≤120)/ `task`(≤`SUBAGENT_TASK_MAX_CHARS` 4000)必填,可选
    `agent_name`(≤40,默认「子代理」)、`instructions`(≤4000,注入子 run 的系统提示)、
    `tools`(≤`SUBAGENT_MAX_TOOLS` 12,必须是父级有效白名单的子集)、
    `timeout_seconds`(30..1800,默认 `SUBAGENT_TIMEOUT_SECONDS` 600);
  - 子 run 落 `runs.parent_kind="subagent"` + `agent_name`/`agent_prompt`/`agent_tools`
    (`retry` 派生的 run 记 `parent_kind="retry"`);继承租户/workspace/`mcp_server_ids`/
    审批策略/LLM 路由,**不**继承父会话、技能快照与 pending 指令,`conversation_id`
    留空;`agent_tools` 为 `null` 时按父级白名单(`agent_tools ?? skill_tools ?? 不限`)
    归一化后落库,子 run 执行时即按它裁剪工具;
  - 生命周期:建子 run(落 `run.created.v1`,payload 带 `parent_run_id`/`parent_kind`/
    `agent_name`)→ 父 run 落 `kind="subagent"` 的 `run.item.v1`(started 带工具面/超时,
    completed 带 `child_status`/`summary`/`usage`)→ **内联**执行子 run(同级
    `plan_run`,不排队)→ 每 1s 轮询;完成取子 run 交付物正文(否则
    `run.completed.v1` 的 results)截断到 `SUBAGENT_SUMMARY_MAX_CHARS`(4000)回喂父模型;
    `awaiting_*` 立即返回(不阻塞父 run,摘要提示去运行详情继续);失败/取消/超时把
    原因写进 item 的 `data.reason`(`failed`/`cancelled`/`timeout`);子 run 被取消
    或父 run 取消时**连带**取消子 run(子 run 落 `run.cancelled.v1`);
  - 用量:`_usage_boundary` 退出时按采集器落一条 `run.usage.v1`(`record_outbox=False`,
    payload `{calls, prompt_tokens, completion_tokens, total_tokens, models}`,无采样不落),
    `GET /runs/{id}/usage` 按模型聚合投影;`GET /runs/{id}/subagents` 列子 run
    (含最近一次摘要与用量);契约见 `docs/design/subagents.md`;
- 生命周期 API:`POST /runs/{id}/plan`、`PATCH /runs/{id}/plan`(等待人工审批时编辑计划)、
  `POST /runs/{id}/approve`、`POST /runs/{id}/reject`、`POST /runs/{id}/resume`
  (blocked → 重新进闸,保留已编辑计划,§6.2 goal 语义)、
  `POST /runs/{id}/retry`(failed → **另起一跑**并继承目标/验收标准/已批准计划,
  返回新 run;血缘见 `parent_run_id` 与 `run.retry.requested.v1`)、
  `POST /runs/{id}/confirm`(危险操作放行/拒绝,§6.2)、
  `POST /runs/{id}/answer`(回答/放弃挂起提问,§10.5;202 返回 run 快照,
  409 `run.invalid_state` / `run.question_mismatch` / `run.question_expired`)、
  `POST /runs/{id}/cancel`(协作式取消:立刻置 `cancelled` + 撤 queued job +
  `run.cancelled.v1`,图结果在下一个执行边界丢弃;终态再取消 409 `run.invalid_state`)、
  `POST /runs/{id}/instructions` + `GET /runs/{id}/instructions`(追加指令,§6.5);
- 对话垂直切片(§6.7):`POST /api/v1/conversations` 建会话、`GET /conversations`(最近活跃在前)、
  `GET /conversations/{id}`(附进行中的 `active_run_id`,前端据此接 SSE)、
  `GET /conversations/{id}/messages` 消息回放、`POST /conversations/{id}/messages` 发消息、
  `POST /conversations/{id}/messages/stream` **SSE 流式**发消息
  (`meta` → (`delta`\* / `tool`\*)\* → `done`,失败以 `error` 帧收尾;直接对话的
  正文边产出边下发;**带工具的对话轮次也是真流式**——每轮模型输出实时进 `delta` 帧,
  工具调用以 `tool` 帧穿插在轮次之间,中间轮次的过渡说明同样下发并随最终 assistant
  回复一起落库(落库正文 = 各轮正文拼接,不再"只保留最后一轮");
  任务是建 run,正文走 run 事件流的 `token.delta.v1`,所以这里只有 `meta`/`done`);
  发消息**先分流**(`services/triage.py`):`mode=auto|chat|task`,auto 由模型判别,chat 直接答一条
  不建 run(`services/chat.py`),task 才建 run 并立即规划或并入当前 run 的追加指令;
  判别失败或无模型时退回 task(确定性桩仍产出计划),显式 chat 而无模型返回 409 `chat.llm_disabled`;
- **配额与预算**(P0-2/P0-3):建 run 前先读 account 的租户配额快照
  (`GET /v1/usage/quota`,`X-Internal-Secret` + `X-Tenant-Id`),月度 token 用尽或并发
  run 超限直接 429(`quota.monthly_tokens_exceeded` / `quota.concurrent_runs_exceeded`,
  details 带 limit/used);account 不可用时**fail-open**(配额是保护性限流,不是鉴权)。
  **已知缺口**:对话直答路径(`services/chat.py`)不走配额闸,见 `docs/gap-2026-10/06-refresh-2026-10-25.md` P0-4。
  配额结构在 `organizations.quota` JSON:`{"monthly_tokens": N, "max_concurrent_runs": M}`,
  缺省即不限制。token 预算用 `AGENT_ORCHESTRATOR__RUN_TOKEN_BUDGET` 控制单次执行的
  模型用量上限(0=不限制),节点边界检查,用尽则该次执行 failed(错误里写明
  `TokenBudgetExceeded`);
- **审批策略**(P0-4):`approval_policies` 表按租户覆盖"要不要人工确认"的默认值,
  `GET/PUT /api/v1/approval-policies` 读/整表替换,规则是
  `{"tool": "<工具名>"|"*", "action": "auto"|"confirm"|"deny"}`。求值顺序:
  精确工具 > `*` > **风险等级默认值**(DANGER 需确认、READ/WRITE 自动),
  空表即等价于旧行为;策略快照随状态进图(节点仍纯函数),命中 `deny` 不执行并把
  原因回喂模型,对话路径也走同一套判定(`confirm` 在 chat 里降级为"请改用任务");
- 状态守卫:非法迁移 409(`run.invalid_state`),统一错误模型来自 `common`;
  规划节点进入时置 `planning`、放行/确认通过时置 `running`(状态变更不单落事件);
  **中断超时**:审批/确认默认关闭、提问默认开启(`APPROVAL_TIMEOUT_SECONDS` /
  `CONFIRMATION_TIMEOUT_SECONDS` / `QUESTION_TIMEOUT_SECONDS` 默认 900,> 0 且
  `JOBS_ENABLED` 时生效),sweeper(`services/interrupts.py`,
  `INTERRUPT_SWEEP_INTERVAL_SECONDS`)把悬挂的审批/确认/提问自动走
  `reject`/`confirm(reject)`/`answer(decline)` 并落 `run.interrupt.timeout.v1`,
  run 可再 `resume` 重新规划;确认超时另补 `run.confirmation.received.v1`,
  提问超时另补 `run.question.timeout.v1` 与 `run.question.answered.v1`
  (`action=decline`、`reason=timeout`),图按固定文案继续;
- 失败与重试(§6.2):失败即终态(`failed` + `run.failed.v1`,原因留
  `status_reason`),**不原地重放**——失败点的检查点不可信,且旧 run 的历史是
  审计凭据,不能被"重试"改写。重试派生新 run 继承已批准计划(计划节点检测到
  已有计划会短路,所以只重走审批闸、不重复调用模型);重试后仍按同一判据放行
  (无危险工具自动放行,有危险工具才需人工),已产生的外部副作用不会被自动撤销;
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
  调用网关时随上下文注入 `X-Tenant-Id` 与 `X-Internal-Secret`(取
  `AGENT_ORCHESTRATOR__INTERNAL_AUTH_SECRET`,与 account/llm-gateway 的
  `INTERNAL_SECRET` 一致),网关据此解析该租户的 BYOK 密钥;密钥留空时只带
  租户头,按平台默认密钥解析。
- 返工循环(§6.1):验收未过 → 回 execute,带验收小结继续返工;≤3 轮;
  耗尽仍不过 → `fail_node` 诚实终结 failed(不伪装 completed);
- 工具调用(架构 §8):`TOOLS_ENABLED=true` 时 execute 走 function-calling——
  模型请求工具 → 进程内工具注册表(`services/tools.py`)或经 `RemoteToolkit`
  远程到 tool-service 执行 → 结果回喂模型,≤5 轮;
  **能力按提供方分组,未开启的不暴露**(能力即权限边界):
  - 本地文档库(`DOCUMENTS_ENABLED`):`list_documents`/`read_document`
    (受限目录只读,后缀白名单、越界/绝对路径拒绝、超限拒读);
  - 企业知识库(`KNOWLEDGE_ENABLED`):`search_knowledge`——经 knowledge-service
    向量检索,让模型"先查资料再回答"(`docs/architecture.md` §9);
  - 内置代码工具(`TOOL_SERVICE_ENABLED`):`read`/`glob`/`grep`/`list_dir`/
    `write`/`edit` 由 tool-service(:8008)提供,本服务不再直读宿主目录;
    旧的代码工具与本机目录配置已随该迁移下线(见 `docs/design/tool-service.md`);
    **工具执行失败不炸图**:错误文本回喂让模型决策,每次调用落
    `run.tool.called.v1` 事件(name/arguments/ok/detail),SSE 实时可见;
- 确认闸只拦**危险操作**(§6.2):工具按 `ToolRisk` 分级——`READ` 直接执行;
  `WRITE`(受控 workspace 文件、沙箱内技能执行、已信任 MCP)在各自权限边界内
  **自动执行**;`DANGER`(对外不可逆写操作、未信任 MCP)**绝不直接执行**——
  execute 收集后交 `confirm_gate` 中断,run 转 `awaiting_confirmation` 并落
  `run.confirmation.required.v1`;`POST /runs/{id}/confirm` 放行则由
  `tool_exec` 执行并回喂,拒绝则跳过并把原因记入 results;确认 ≤3 轮防死循环。
- 交互提问(§10):任务图工具箱 `with_interaction()` 才暴露 `ask_question`
  (`kind=interaction`;对话/只读 MCP 面一律过滤),execute 命中该调用时本回合
  **只处理提问**——同轮其余工具回 `tool_error` 不执行(中断前零副作用);参数
  非法、被租户策略禁用或达轮次上限(`MAX_ASK_CYCLES=3`)则回喂模型、不挂起。
  `ask_gate` 是唯一新增 interrupt 点:run 转 `awaiting_input` 并落
  `run.question.required.v1{question_id, questions, allow_custom, expires_at}`,
  回答写回对应 tool-role 消息后继续执行;`decline`/超时用固定文案继续。
- 计划自动放行(§6.2):审批闸默认只在**装配了 DANGER 工具**时拦截——基础工具 +
  该 run 引用的 MCP 工具经技能 tools 白名单裁剪后无 DANGER 级,则 plan 之后直接进
  execute(事件 `run.planned.v1` 的 `auto_approved=true`,并补 `run.approved.v1`);
  开关 `AGENT_ORCHESTRATOR__PLAN_AUTO_APPROVE`(默认 true,关闭即回到每跑必审)。
  执行期危险操作确认闸(`awaiting_confirmation`)不受影响,危险动作永远人工确认。

- 鉴权(架构 §10):默认开启——校验 `Authorization: Bearer <IdP access token>`
  (OIDC 发现 + JWKS,RS256;自建 IdP 即 account-service :8001),租户取自
  token claim(默认 `tenant_id`)——**开启时伪造 `X-Tenant-Id` 无效**;显式
  关闭鉴权(仅特殊部署)才沿用临时头,缺失即 401 `auth.header_missing`;
- 服务间调用 token(§5):本服务对下游(knowledge/workspace)的内部
  调用**一律**经 `TokenBroker`(`services/token_broker.py`)用共享内部密钥
  (`INTERNAL_AUTH_SECRET`,配置 `AGENT_ORCHESTRATOR__INTERNAL_AUTH_SECRET`,
  与 account 的 `ACCOUNT__INTERNAL_SECRET` 一致)向 account 的
  `POST /v1/auth/issue` 换"以目标租户为 claim"的标准 token,进程内按租户
  缓存到过期前 60s——**与用户面走同一个 JwtVerifier,不设后门**;不再有
  `X-Tenant-Id` 裸调路径,密钥未配置时在换发处即报错;阶段 B 换 mTLS/OBO 时
  只换 TokenBroker 实现,三个下游客户端的注入点不变;

- 追加指令(§6.5):`POST /runs/{id}/instructions` 排队(终态 run 追加 409),
  在**下一个人工边界**消费——规划(plan/resume)、放行(approve)、
  危险操作确认(confirm);消费即标记 `consumed` 并落
  `run.instruction.queued.v1` / `run.instruction.consumed.v1`;内容经
  `extra_instructions` 并入 plan/execute/verify/deliver 的提示词(模型必须遵循)。

- 交付物(§7):一个 run 可以有**多件**交付物(`run_artifacts` 已去唯一约束)。
  deliver 合成的成品报告是一件(`source=report`),技能产物每个文件一件
  (`source=skill`),工具产出的外链文件每件一个(`source=mcp`,见下);
  取件 `GET /runs/{id}/artifacts`(列表)/ `GET /runs/{id}/artifacts/{id}/download`
  (原始字节 + 附件头;旧的 `GET /runs/{id}/artifact` 仍返回主交付物文本,兼容保留);
  事件 `run.delivered.v1` 记 `artifact_ids` 与逐件的 source/filename/content_type/
  size_bytes。未交付取件 404 `artifact.not_found`,failed 终态不产交付物、也不抓外链;
- 交付物内容:文本类(`text/*`、JSON/YAML/XML 等)进 `content` 列,二进制进
  `content_bytes`;存储后端 `ARTIFACT_STORE=database`(默认)或 `filesystem`
  (正文落 `ARTIFACT_ROOT`,行里只留 `object_key`/`size_bytes`;键按
  `<租户>/<run>/<交付物>` 分层,后缀取 MIME 对应扩展名,拒绝目录穿越);对象丢失
  → 500 `artifact.object_missing`。换 S3 兼容存储只需实现
  `common.artifacts.ArtifactStore`;
- 工具产物落成文件(§7,MCP 外链):工具返回的记录里出现白名单扩展名的 http(s)
  链接(`services/file_capture.py`,`.png/.pdf/.xlsx/.docx/.zip/.csv/.md` 等 18 种)
  时,执行期就把它**下载成真实交付物**,而不是只留一段链接文本——带签名链接会过期,
  模型交付后再下载往往已经失效。约束:`ARTIFACT_DOWNLOAD_ENABLED`(默认 true)、
  `ARTIFACT_DOWNLOAD_MAX_FILES`(默认 10,按 run 去重)/`MAX_BYTES`(10MB)/
  `TIMEOUT_SECONDS`(20s);私网与回环地址直接拒绝(SSRF)、不跟随重定向、
  `Content-Disposition` 的 `filename*` 优先取名;抓取失败如实记进
  `run.delivered.v1` 的 `download_errors`,**不拖垮交付**(报告照常落库);
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
- 交付物镜像(`WORKSPACE_ENABLED=true`):交付时逐件推给 workspace-service
  (§7 里 artifacts 归属 workspace),文本走 `content`、二进制走 `content_base64`;
  回写的 `workspace_artifact_id` 存在行上,**重复镜像直接复用**(幂等);
  成败记进 `run.delivered.v1` 的 `workspace_artifact_ids` / `workspace_errors`
  ——镜像失败不丢交付物。
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
- **MCP 在对话内 + 审批策略(P2 之后补)**:chat 直接对话与执行图共用同一个
  extras 工厂(`main._load_extras`),所以对话里也能看到并使用 MCP 工具;但对话
  没有审批流(架构 §6.7),因此**每个 server 一个 `auto_approve` 信任开关**
  (Web `/mcp` 页「免确认执行」,默认关):关时 `risk=DANGER`(未信任 = 对外不可逆),
  对话里只回喂"请改用任务"、run 里计划需人工审批且工具逐次确认;开时 `risk=WRITE`(受控写),
  对话与 run 都直接执行——就是架构 §6.4 "操作策略是配置,不是硬编码"。
  extras 取失败只降级外部工具,不影响对话本身。
- **对话/任务的 MCP 显式引用**:`MessageCreate.mcp_server_ids` 与
  `RunCreate.mcp_server_ids`(≤10,去重保序)让用户逐条收窄外部工具——选中后
  extras 工厂只装配被引用 server 的工具(`tool_specs(server_ids=...)`),不选仍是
  全量;引用随消息(`chat_messages.mcp_server_ids`)与 run(`runs.mcp_server_ids`)
  落库、run 建好后固化为快照,并以提示词片段告诉模型"未列出的本次不可用"。
  校验在落库前完成:未知 404 `mcp.server_not_found`、停用 409
  `mcp.server_disabled`、`MCP_ENABLED=false` 时 409 `mcp.disabled`;Web 对话页
  输入区有「MCP」选择器,历史消息显示引用徽标。

- **技能包上传(P3)**:用户上传 SKILL.md 包(zip,根目录必须含 SKILL.md,
  允许单一顶层目录;可选 skill.json 描述 id/version/title/description/
  entrypoint/runtime/network/timeout_seconds/tools)。服务端校验:包体 20MB /
  500 文件 / 解压后 50MB 上限,路径穿越、绝对路径、符号链接、NUL、重复路径、
  skill.json 非法、缺 SKILL.md 一律 400;zip 存进 workspace-service 通用对象 API
  (`WORKSPACE_ENABLED=true` 时装配 WorkspaceClient;未装配时上传 503
  `skill.storage_unavailable`,不静默降级),DB 只存对象引用 + manifest/skill_md
  快照。按"租户内 slug + version"版本化:
  - `POST /skills/packages`(multipart 字段 file)→ 201 包详情(含全部 versions);
  - `GET /skills/packages` / `GET /skills/packages/{id}` / `DELETE /skills/packages/{id}`
    (删除 DB 包/版本 + best-effort 删对象,对象删除失败不阻塞);
  - `GET /skills` 内置 + 每个上传包**最新版本**合并(source=builtin|uploaded;
    上传包带 has_scripts/package_id/version_id,有 entrypoint 自动补 skill_run 工具);
  - `GET /skills/{skill_id}` 内置优先,其次上传包 slug;
  - run 创建时把上传包快照冻结进 runs(`skill_version_id/object_key/sha256/
entrypoint/runtime/network/timeout_seconds`),之后上传新版本不影响已建 run;
  - 冲突:同包同版本重复上传 409 `skill.version_exists`,上传 id 与内置技能
    冲突 409 `skill.id_conflict`。

- **技能包执行(P3-C)**:run 绑定了**有 entrypoint** 的上传包时,execute 的工具
  注册表额外装配 `skill_run`(`services/skill_runner.py`;受控写 WRITE,在一次性
  沙箱里自动执行,不打断审批)。需要 `SKILL_EXECUTION_ENABLED=true` **且** `SANDBOX_ENABLED=true`
  与 `WORKSPACE_ENABLED=true`(缺任一不暴露,宁可不给能力也不在本进程跑上传代码):
  - 一次性沙箱(`kind=skill`,镜像按快照 runtime 取 `SKILL_PYTHON_IMAGE/
SKILL_NODE_IMAGE`,TTL `SKILL_SANDBOX_TTL_SECONDS`,网络取快照
    `skill_network`,默认 deny);
  - 取包后**校验 sha256 与 run 快照一致**,再写 zip + 解包脚本,在沙箱内用
    `python3` 解包——路径穿越/绝对路径/符号链接/文件数/解压总量按上传同口径
    **再校验一遍**(双保险);entrypoint 的 argv 来自快照,运行时决定解释器
    (`python→python3`/`node→node`,首段已是解释器则原样用),cwd=`/workspace/skill`,
    stdin 传 `{"input","goal","title"}` 的 JSON,超时取快照与
    `SKILL_EXEC_TIMEOUT_SECONDS` 的小值 +30;
  - 产物读 `/workspace/output`:最多 `SKILL_MAX_OUTPUT_FILES` 个,单文件正文
    上限 `SKILL_MAX_OUTPUT_BYTES`(超限/二进制只报元数据),`stdout`/`stderr` 截断后回喂模型;
  - `/workspace/output` 里**每个文件独立登记**为一件交付物(`source=skill`,
    title=相对路径,二进制一并留字节、不再只报元数据);登记失败只写进 summary,
    不让工具失败;交付阶段另落报告件(与技能产物并存,不覆盖);每次执行
    **必销毁**沙箱;
  - chat 路径没有 `run_id`,`skill_run` 不会出现在对话直答的工具列表里
    (能力边界由 ToolContext 决定)。

后续补齐:更多工具与技能、S3 兼容交付物存储、Redis Streams 直播(替代 SSE 轮询)。

命令(仓库根执行):`npx nx run agent-orchestrator:serve`(:8002)/ `npx nx test agent-orchestrator` / `npx nx lint agent-orchestrator`
