# events

beauty-moon 事件共享库。架构定位见 `docs/architecture.md` §6.3 与 §5。

当前(骨架阶段)内容:

- `events.envelope.EventEnvelope`:事件信封 + 命名/版本约定(`<域>.<实体>.<动作>.v<版本>`,版本只增不改)

随实现逐步补齐:事件总线抽象(Redis Streams 适配)、outbox 助手、消费幂等夹具。

事件清单(起步):`run.started.v1` / `token.delta.v1` / `tool.invoked.v1` / `run.completed.v1` / `email.received.v1`。

命令(仓库根执行):`npx nx lint events` / `npx nx format events` / `npx nx test events` / `npx nx sync events`
