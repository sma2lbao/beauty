# 站内通知持久化(P1-J)

> 旧实现(`api/v1/notifications.py`)只在 SSE 连接存活期间做内存 diff:关掉页面通知就没了,没有未读态,`Last-Event-ID` 也只是兼容占位、不做回放。现在的做法是把"需要人关注"的 run 状态**幂等投影成 `notifications` 行**,由后台 sweeper 写入,`SSE` 退化成这张表的增量尾巴。

实现位置:`services/agent-orchestrator/agent_orchestrator/` 下的 `models/notification.py`、`repositories/notification.py`、`services/notifications.py`、`schemas/notifications.py`、`api/v1/notifications.py`;迁移 `migrations/versions/c4e8b2d6f1a9_notifications.py`。

## 1. 口径

需要提醒的状态与前端 `NOTIFY_ON` 一致,共 7 个:`awaiting_approval`、`awaiting_confirmation`、`awaiting_input`、`completed`、`failed`、`blocked`、`cancelled`。状态 → (文案, 色调) 的映射落在服务端 `_STATUS_META`,色调随行落库,客户端不再自己映射。

事实来源仍是 `run_events` 与 `runs`:通知是**投影**,丢了可以再扫出来,所以不追求 exactly-once,只追求幂等。

## 2. 表结构

```
notifications
  id               string(32)  pk
  account_id       string(32)  not null
  kind             string(32)  not null          -- 目前只有 run.status
  dedupe_key       string(160) not null          -- run_id:status
  title            string(200) not null
  body             text        not null default '' -- (goal or run_title)[:300]
  tone             string(16)  not null default 'info' -- info/success/warning/danger
  to_path          string(200) null              -- /?conversation=<id> 或 /runs
  run_id           string(32)  null
  conversation_id  string(32)  null
  payload          json        not null default {} -- run_id/run_title/goal/status
  read_at          timestamp   null              -- null 表示未读
  created_at       timestamp   not null
  updated_at       timestamp   not null
```

约束与索引:

- `uq_notifications_account_kind_dedupe(account_id, kind, dedupe_key)`:同名通知在账号内只能有一条。
- `ix_notifications_account_created_at(account_id, created_at)`:列表倒序与游标分页。
- `ix_notifications_account_read_at(account_id, read_at)`:未读计数。
- `ix_notifications_run_id`:按 run 反查。

## 3. 写入流程

写入不在请求路径上,由容器启动的后台 sweeper 完成(`core/container.py` 的 `notification_sweep_task`),因此**用户关掉页面也照样留下未读**。

```
sweep_once(factory, settings, now=None) -> {"created", "purged"}
  since = now - notifications_lookback_seconds
  RunRepository.list_notifiable_changed(since, statuses=NOTIFY_STATUSES,
                                       limit=notifications_batch_limit)
  for run in runs:
      notification_for(run)              # 状态 -> 通知字段,中间态返回 None
      NotificationRepository.insert_if_absent(account_id=run.account_id, ...)
  NotificationRepository.purge_read_before(now - notifications_retention_days)
```

`list_notifiable_changed` 是**跨账号**的系统维护查询(后台任务没有请求账号上下文),因此不经 `AccountRepository` 的账号隔离;其余读写全部按 `account_id` 过滤。

幂等靠数据库:M`PG` 用 `insert().on_conflict_do_nothing(index_elements=[account_id, kind, dedupe_key])`,`SQLite` 用 `sqlite_insert` 同样写法;其他方言退化为先查后写。多副本同时扫同一批 run 时,只有一行写入成功,其余静默丢弃(`rowcount == 0`),不会整批失败。

## 4. 游标

游标是 `<created_at 的 isoformat>|<id>`,id 做 tie-break(同微秒写入时保证顺序稳定)。

- 列表分页:`before=next_cursor`,服务端取 `limit + 1` 判断是否还有下一页。
- SSE 回放:`Last-Event-ID` 就是同一格式,服务端用它查 `list_after`(正序),把游标之后的行逐条推完。

两者共用一个编码,前端不需要区分"分页游标"和"事件 id"。

## 5. 端点

| 方法 | 路径                              | 说明                                                             |
| ---- | --------------------------------- | ---------------------------------------------------------------- |
| GET  | `/api/v1/notifications`           | 列表(未读+已读,时间倒序),查询 `limit`(1..100,默认 20)与 `before` |
| POST | `/api/v1/notifications/{id}/read` | 单条已读;不存在或不属于本账号 → 404 `notification.not_found`     |
| POST | `/api/v1/notifications/read-all`  | 全部已读,返回 `{"updated": n}`                                   |
| GET  | `/api/v1/notifications/stream`    | SSE 增量流                                                       |

列表响应:`{"items": [NotificationRead], "unread_count": n, "next_cursor": string | null}`。

SSE 帧形状:首帧固定 `: ready`,之后每条通知一帧 `event: notification.v1` + `id: <cursor>` + `data: <NotificationRead JSON>`;没有新行时按 `sse_keepalive_polls` 打 `: keep-alive`。带 `Last-Event-ID` 时先回放再进入轮询。

## 6. 前端

- `apps/web/src/lib/notifications-api.ts`:列表、单条已读、全部已读三个调用,以及 `streamNotifications`(解析 `notification.v1`)。
- `apps/web/src/features/notifications/use-notifications-model.ts`:`react-query` 拉列表(`queryKeys.notifications`),`refetchInterval` 30s 作为 SSE 不可用时的兜底;已读 mutation 后按前缀失效。
- `apps/web/src/lib/notification-stream.ts`:`useNotificationStream()` 只做两件事 —— 收到推送就失效列表缓存,以及在"非当前会话"时弹一条 toast(指数退避重连)。
- `apps/web/src/features/notifications/notification-center.tsx`:未读判定改为 `read_at === null`,点击单条先回写已读再跳 `to_path`。
- `apps/web/src/stores/notifications-store.ts` 收敛成纯 toast 池(不再持久化通知数组与 `readCursor`,已读位置以服务端为准)。

## 7. 配置

| 键                                     | 默认   | 说明                                           |
| -------------------------------------- | ------ | ---------------------------------------------- |
| `NOTIFICATIONS_ENABLED`                | `true` | 关掉则不启动 sweeper(端点仍可读表)             |
| `NOTIFICATIONS_SWEEP_INTERVAL_SECONDS` | `30.0` | `<= 0` 时只读表、不再写入                      |
| `NOTIFICATIONS_RETENTION_DAYS`         | `30`   | 只清**已读**旧行                               |
| `NOTIFICATIONS_LOOKBACK_SECONDS`       | `120`  | 每轮扫"最近 N 秒内变更"的 run                  |
| `NOTIFICATIONS_BATCH_LIMIT`            | `100`  | 单轮处理的 run 条数上限(也是 SSE 单次回放上限) |
| `NOTIFICATIONS_POLL_INTERVAL_SECONDS`  | `2.0`  | SSE 无新行时的轮询间隔                         |
| `SSE_KEEPALIVE_POLLS`                  | 既有   | 每 N 轮空转打一条 keep-alive                   |

## 8. 保留期

只清已读:`purge_read_before(cutoff)` 删 `read_at is not null and created_at < cutoff` 的行。未读是待办,不自动删 —— 用户没看过就不该消失。

## 9. 为什么不用 outbox

outbox 解决的是**跨服务**副作用投递(状态改了事件一定发)。通知的订阅方是本服务自己的读路径,数据源是同一库的 `runs` 表,没有第二个进程需要收事件;而且写入本身已经幂等(唯一约束),重复扫同一批 run 无害。所以这里用"扫表 + 幂等写"而不是 outbox:少一张队列表、少一次投递、少一处失败模式,代价是延迟等于 sweeper 间隔(默认 30s,仍在 SSE 之外由列表兜底)。

## 10. 测试

- 后端 `services/agent-orchestrator/tests/test_notifications.py`(11 例):7 种状态的落表字段、中间态不产通知、游标编解码与非法值、`sweep_once` 幂等 + 保留期只清已读、列表分页与未读数、单条/全部已读与跨账号 404、SSE 首帧与按游标回放、keep-alive、无令牌 401、sweeper 间隔为 0 与异常不退出。
- 前端:`apps/web/src/lib/notifications-api.test.ts`、`apps/web/src/lib/notification-stream.test.tsx`、`apps/web/src/features/notifications/notification-center.test.tsx`、`apps/web/src/stores/notifications-store.test.ts`(合计 16 例)。
