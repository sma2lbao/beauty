# agent-orchestrator

Agent 编排服务(阶段 A 骨架):会话与运行编排的控制面。架构与执行模型见 `docs/architecture.md` §6。

当前实现(阶段 A):

- run 垂直切片:`POST /api/v1/runs` 创建,`GET /runs/{id}` 查询,
  `GET /runs/{id}/events` 事件回放(§6.3 事件溯源读端);
  `GET /runs/{id}/events/stream` SSE 事件流:重放 + 实时推送,`Last-Event-ID`
  断线续传,run 终态自动 `stream.end` 收线;
- LangGraph 执行图(`agent_orchestrator/graph/`):plan → 审批闸(interrupt 人工确认)
  → execute → verify(返工 ≤3 轮)→ deliver;checkpointer 按 DATABASE_URL 自动选择:
  postgres → `langgraph-checkpoint-postgres`(图线程跨进程可恢复),sqlite/dev → InMemorySaver;
- 生命周期 API:`POST /runs/{id}/plan`、`PATCH /runs/{id}/plan`(审批前编辑计划)、
  `POST /runs/{id}/approve`、`POST /runs/{id}/reject`、`POST /runs/{id}/resume`
  (blocked → 重新进闸,保留已编辑计划,§6.2 goal 语义);
- 状态守卫:非法迁移 409(`run.invalid_state`),统一错误模型来自 `common`;
- `/healthz` `/readyz`:存活/就绪探针。

- schema 管理:alembic(async env,DB URL 与运行服务同源,见 `migrations/env.py`);
  `uv run alembic upgrade head` / `uv run alembic downgrade base`,命名约定保证回滚无损。

节点是确定性桩:LLM 规划/执行经 llm-gateway 接入(阶段 B),接入点见 `graph/nodes.py` 各 docstring。
后续补齐:技能注册表、指令队列消费、IdP JWT 租户、Redis Streams 直播(替代 SSE 轮询)。

命令(仓库根执行):`npx nx run agent-orchestrator:serve`(:8002)/ `npx nx test agent-orchestrator` / `npx nx lint agent-orchestrator`
