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
  `POST /runs/{id}/confirm`(写操作放行/拒绝,§6.2);
- 状态守卫:非法迁移 409(`run.invalid_state`),统一错误模型来自 `common`;
- `/healthz` `/readyz`:存活/就绪探针;
- schema 管理:alembic(async env,DB URL 与运行服务同源,见 `migrations/env.py`);
  `uv run alembic upgrade head` / `uv run alembic downgrade base`,命名约定保证回滚无损;
- 生命周期含 failed 终态:图执行(plan/execute)异常在调用边界统一收敛为
  `run.failed.v1` 事件 + 状态落库,不向调用方裸奔(§6.2);
- LLM 接入(架构 §5 唯一出口):`LLM_ENABLED=true` 时 plan/execute/verify 经
  `llm-gateway` 生成计划、执行结果与逐条验收判定(节点工厂注入,见 `graph/nodes.py`);
  false 或网关未起时回退确定性桩。测试注入 FakeLLM,零网络零 token。
- 返工循环(§6.1):验收未过 → 回 execute,带验收小结继续返工;≤3 轮;
  耗尽仍不过 → `fail_node` 诚实终结 failed(不伪装 completed);
- 工具调用(架构 §8):`TOOLS_ENABLED=true` 时 execute 走 function-calling——
  模型请求工具 → 工具注册表(`services/tools.py`)执行 → 结果回喂模型,≤3 轮;
  工具经 `connector-service` 出去(`list_connections`/`fetch_emails` 只读,
  `send_email` 为写操作);**工具执行失败不炸图**:错误文本回喂让模型决策,
  每次调用落 `run.tool.called.v1` 事件(name/arguments/ok/detail),SSE 实时可见;
- 写操作确认闸(§6.2):写工具(requires_confirmation)**绝不直接执行**——
  execute 收集后交 `confirm_gate` 中断,run 转 `awaiting_confirmation` 并落
  `run.confirmation.required.v1`;`POST /runs/{id}/confirm` 放行则由
  `tool_exec` 执行并回喂,拒绝则跳过并把原因记入 results;确认 ≤3 轮防死循环。

- 交付物(§7):deliver 由 LLM 合成最终成品,落 `run_artifacts`(每 run 一件);
  取件 `GET /runs/{id}/artifact`,事件流记 `run.delivered.v1`(artifact_id/chars);
  未交付取件 404 `artifact.not_found`,failed 终态不产交付物。

后续补齐:更多工具与技能、交付物转对象存储、指令队列消费、
IdP JWT 租户、Redis Streams 直播(替代 SSE 轮询)。

命令(仓库根执行):`npx nx run agent-orchestrator:serve`(:8002)/ `npx nx test agent-orchestrator` / `npx nx lint agent-orchestrator`
