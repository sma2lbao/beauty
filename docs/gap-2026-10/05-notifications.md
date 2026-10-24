# 交互面 · 通知能力实现记录（2026-10-18）

> 对应 `00-summary.md` P1-9 的「通知」项与 `01-repo-inventory.md` §4 / §8 L3。
> **本轮已完成 P1-9b 的服务端推送部分**：orchestrator 新增租户级通知 SSE 端点，前端优先订阅推送、轮询兜底；剩余桌面系统通知与通知偏好。

## 1. 能力清单

| 能力                                              | 实现                                                                                  | 位置                                                      |
| ------------------------------------------------- | ------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| 通知中心（铃铛 + 未读角标 + 下拉面板 + 全部已读） | 侧栏「更多」区常驻 + 移动端顶栏（`lg:hidden`）                                        | `apps/web/src/components/console/notification-center.tsx` |
| toast 浮层（右下角、自动过期、手动关闭）          | 挂在控制台布局层，任何页面可见                                                        | `apps/web/src/components/console/toast-viewport.tsx`      |
| 通知状态与持久化                                  | zustand + persist：通知列表（上限 50）+ 已读游标；toast 为瞬态不持久化                | `apps/web/src/stores/notifications-store.ts`              |
| 触发源：run 状态变化                              | 复用全局 `['runs']` 5s 轮询做 diff；首次见到只建基线（刷新不弹历史）                  | `apps/web/src/lib/run-status-watcher.ts`                  |
| 触发源：当前会话判断                              | chat 页写入 `currentConversationId`，当前会话内的变化只进列表不弹 toast（避免双打扰） | `apps/web/src/stores/ui-store.ts`、`pages/chat.tsx`       |

**通知哪些状态**：`awaiting_approval`（待审批）、`awaiting_confirmation`（待确认写操作）→ warning；`completed` → success；`failed` / `blocked` → danger。中间态（`pending → running` 等）不打扰。

## 2. 设计取舍

1. **为什么用前端轮询 diff 而不是后端推**：`/runs` 已经是 5s 轮询且与侧栏共享同一 `['runs']` query key，diff 成本近乎为零、后端零改动、事件语义不需要新表。代价是**通知依赖页面开着**。
2. **为什么挂布局层而非侧栏**：侧栏可隐藏（组件卸载会中断探测），布局层常驻。
3. **已读用游标而非逐条标记**：避免把整张通知表写进 localStorage；50 条上限防止无限增长。
4. **当前会话降噪**：用户正盯着的会话已经有状态条与执行面板，再弹 toast 是噪音——只入列表。
5. **入口两处**：桌面端在侧栏底部（与账户区相邻），移动端顶栏——不新开一页、不打断现有布局。

## 3. 验证

- `npx nx typecheck @beauty/web` ✅
- `npx nx test @beauty/web -- --run` ✅ 204 tests（新增 12 条：store 5、watcher 纯函数 3、组件交互 4）
- `npx nx lint @beauty/web` ✅
- dev server（`:5173`）按需编译新模块全部 200

## 4. 服务端推送（P1-9b 已完成）

| 层         | 实现                                                                                                                                                                                                                       |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 后端端点   | `GET /api/v1/notifications/stream`（`services/agent-orchestrator/agent_orchestrator/api/v1/notifications.py`）：首帧 `: ready` 后每轮扫描 `updated_at` 落在活跃窗口内的 run，与前一轮快照 diff，只推**需要提醒**的状态变化 |
| 仓储       | `RunRepository.list_changed_since()`：`updated_at >= since` + 租户过滤，避免全表扫描                                                                                                                                       |
| 配置       | `notifications_poll_interval_seconds`（默认 2s）/ `notifications_lookback_seconds`（120s）/ `notifications_batch_limit`（100）                                                                                             |
| 前端客户端 | `apps/web/src/lib/notifications-api.ts`：手写 SSE frame 解析（带 Bearer 头，EventSource 做不到）                                                                                                                           |
| 前端接线   | `run-status-watcher.ts`：SSE 优先 + 指数退避重连（1s→30s）；轮询仍保留作兜底；同一 `runId:status` 去重，避免两条通道重复通知                                                                                               |

**设计取舍**：通知不落表、不做回放——它是事件流（`run_events`）的「提醒投影」，漏一帧等价于多一次轮询，客户端会从 `/runs` 列表补齐真实状态。真正需要「关页面也能收」时（邮件/桌面推送），再落 notifications 表接 outbox。

**端到端验证**（本地 dev 栈实测）：

```
curl -N -H "Authorization: Bearer $TOK" .../api/v1/notifications/stream
: ready

id: 1
event: run.status_changed.v1
data: {"run_id":"6e779...","title":"通知联调","status":"awaiting_approval",...}
```

## 5. 后续（剩余项）

1. **服务端推送**：outbox 已有 `run.completed.v1` 等事件，可加 SSE `/api/v1/notifications/stream`（按租户），前端从轮询 diff 切到事件驱动；关页面时的「跑完提醒」需要这一层。
2. **桌面系统通知**：浏览器 `Notification` API + 用户授权开关（放在设置页「通知偏好」）。
3. **通知偏好**：哪些状态通知、是否弹 toast、静音时段——落在 account-service 的用户偏好表。
4. **邮件后置**：与 M4-02 一致，站内优先。
