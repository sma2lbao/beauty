# agent-orchestrator

Agent 编排服务(阶段 A 骨架):会话与运行编排的控制面。架构与执行模型见 `docs/architecture.md` §6。

当前骨架实现了 run 生命周期的第一条垂直切片:

- `POST /api/v1/runs`:创建 run(pending 状态)+ 追加 `run.created.v1` 事件;
- `GET /api/v1/runs/{id}` / `GET /api/v1/runs/{id}/events`:查询与事件回放(§6.3 事件溯源的读端);
- `/healthz` `/readyz`:存活/就绪探针。

后续补齐:LangGraph 状态机与 checkpointer(plan→confirm→execute→verify→deliver)、SSE 出口、
技能注册表、指令队列消费、alembic 迁移、接入 IdP JWT 租户。

命令(仓库根执行):`npx nx run agent-orchestrator:serve`(:8002)/ `npx nx test agent-orchestrator` / `npx nx lint agent-orchestrator`
