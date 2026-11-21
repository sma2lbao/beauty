# 持久 shell 的跨实例归属与接管

> 状态:已落地(2026-11-15),P1-L/M2 后半。
> 代码:`services/workspace-service/workspace/services/shell_sessions/`(registry/placement/pump/manager/events/state)、`workspace/models/shell.py`、迁移 `b6f1d3c8e2a7_shell_sessions.py`。
> 关联:[gap-2026-11/00-refresh-2026-11-09.md](../gap-2026-11/00-refresh-2026-11-09.md) P1-L、[background-jobs.md](./background-jobs.md)(同批工具面能力)。

## 1. 问题

`POST /v1/sandboxes/{id}/exec` 是一次性 exec:每条命令一个独立进程,`cd`/`env`/交互式程序都不成立。为此加了持久 shell 会话(开会话 → 写 stdin → SSE 读输出 → 关闭),但**会话状态原本只活在进程内存里**:进程内的字典键是 `(account_id, sandbox_id, session_id)`,多副本部署时同一 `session_id` 的后续请求落到别的实例只能得到 404,进程重启则整个会话消失。目标是让这条链路在**多副本与重启**下行为可解释、可恢复。

先厘清一件容易混淆的事:会话的"身份"和会话的"输出"是两类数据。

- **会话本身**(在哪个沙箱、provider 侧进程号、cwd、尺寸、状态、归属谁)是可以外置的小数据,总共几百字节;
- **输出流**(ring buffer、`seq`、SSE 订阅者队列)是高写入的进程内状态,每会话 64KB ring + 若干队列。

本设计只外置前者。后者仍在**持有实例**(owner)的内存里,因此"接管"的语义是**接着往下走**,不是"无缝迁移"——接管前的输出不回放,见 §8。

## 2. 方案对比

| 方案                                     | 做法                                                                             | 问题                                                                                                                                |
| ---------------------------------------- | -------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| A. 粘性路由                              | 网关按 `session_id` 哈希固定到某实例                                             | 只解决"路由到对的人",不解决实例死亡;网关要维护哈希与实例表,滚动升级期间会话照样丢                                                   |
| B. 输出外置                              | ring buffer 与订阅者搬到 Redis Stream / NATS                                     | 要引入新依赖(workspace-service 当前无 redis 客户端)与一条新的热路径,收益主要是"任意实例都能读",但写 stdin 仍只能由持有 pty 的实例做 |
| C. 会话目录 + 所有者租约 + provider 重连 | 小数据落库,持有者用租约声明所有权;失去持有者时,新实例经 provider 按 pid 重新接管 | 只对支持"按 pid 重连"的 provider 有效(e2b 支持 `pty.connect`;local 与 microsandbox 不支持)                                          |

选择 **C**。理由:它把"会话在哪"变成一条可查询的事实,把"谁在跑"变成一条可过期的租约,不改输出热路径、不新增基础设施依赖;而且它天然覆盖真实痛点——8 个 Python 服务当前是单机 Docker Compose(见 [hybrid-deployment.md](./hybrid-deployment.md)),"多副本"不是今天的现实,**"进程重启/滚动升级后会话怎么办"才是**。方案 B 保留为后续(§9)。

## 3. 数据模型

新表 `shell_sessions`(模型 `workspace/models/shell.py::ShellSessionRecord`,`AccountMixin` 自动带 `account_id`):

| 列                                                            | 说明                                                                                                   |
| ------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `sandbox_id` / `session_id`                                   | 会话标识;唯一键 `(account_id, sandbox_id, session_id)`                                                 |
| `provider`                                                    | 创建会话的 provider 名(`e2b`/`microsandbox`/`local`;`sandboxd` 可创建但无 pid,不会接管),接管时必须匹配 |
| `runtime_name`                                                | provider 侧的沙箱名,接管时用来找回沙箱                                                                 |
| `provider_pid`                                                | provider 侧进程号(e2b 的 pty pid);空表示不可接管                                                       |
| `cwd` / `cols` / `rows` / `tty`                               | 接管时恢复终端用                                                                                       |
| `status`                                                      | `running` / `closed` / `lost`                                                                          |
| `owner_instance`                                              | 当前持有者实例身份                                                                                     |
| `lease_expires_at`                                            | 租约到期时间;过期即视为无主                                                                            |
| `last_activity_at` / `created_at` / `closed_at` / `exit_code` | 观测与回收用                                                                                           |

索引:`Index("ix_shell_sessions_lease", "status", "lease_expires_at")` 供孤儿扫描;`owner_instance` 与 `sandbox_id` 单列索引。

不变量:

- 同一 `(account_id, sandbox_id, session_id)` 至多一行;
- `status=running` 且 `lease_expires_at > now` 的行,只有 `owner_instance` 有权操作其 pty;
- `status=closed` 的行只用于回答"它已经关了",不再持有进程;
- `status=lost` 表示"登记过但进程已不可达",同样不再持有进程。

## 4. 实例身份与租约

实例身份 `resolve_instance_id(settings)`:

1. 显式 `WORKSPACE__INSTANCE_ID` 优先(去空白);
2. 否则 `f"{socket.gethostname()}:{os.getpid()}"`。

pid 参与身份是有意的:同一台机器上多 worker 时,两个进程不能同时认为自己持有同一个 pty,否则会**双写同一个 stdin**。代价是进程重启后 `instance_id` 变了,旧会话不再"属于自己",必须走租约过期路径才能接管——最长等一个租约周期(默认 120s)。

租约:`WORKSPACE__SANDBOX_SHELL_LEASE_SECONDS`(默认 120s),持有实例每轮 `sweep`(默认 30s 一次,见 `SANDBOX_SHELL_SWEEP_INTERVAL_SECONDS`)对自己仍活着的会话续租。120s = 4 个 sweep 周期,给一次 GC 抖动/慢查询留余量;故意不做得更短,因为误判"持有者已死"会导致两个实例同时操作同一个 pty。

sweep 里的顺序是:先按空闲 TTL 关会话 → 再续租 → 最后回收孤儿。先续租可以让本轮仍活着的会话不被自己当孤儿捡走。

## 5. 关键时序

### 5.1 开会话

`open` → 限流(`max_per_sandbox`,`count_active` 现在查表:只数 `status=running` 且租约未过期的行)→ `provider.open_shell` → `registry.register`(`owner_instance=self`、`lease=now+lease_seconds`、`provider_pid=getattr(shell, "pid", None)`)→ 建内存 state 与读循环。登记失败时先 `shell.close()` 再抛,避免留下没有账的 pty。

### 5.2 写输入 / 订阅 / 关闭

三个端点都先 `ensure`:`write`/`close` 在管理器内部 `_ensure`,SSE `stream` 端点在 `subscribe` 前显式 `await shell_sessions.ensure(...)`(订阅必须先判归属,否则会订阅到一个本地不存在的会话)。`ensure` 的判定顺序:

1. 本地 state 命中 → 直接用(最常见路径,零数据库往返);
2. 查登记表,不存在 → 404 `sandbox.session_not_found`;
3. `status=closed` → 409 `sandbox.session_closed`;`status=lost` → 409 `sandbox.session_lost`;
4. 归属是别的实例且租约未过期 → 409 `sandbox.session_elsewhere`(`details` 带 `session_id`/`owner_instance`/`provider`/`resumable`);
5. 否则(自己重启留下的、或租约已过期)→ 尝试接管(§5.3)。

### 5.3 接管

`_takeover` 分两步,**先仲裁再碰 pty**:

1. 原子认领:`UPDATE shell_sessions SET owner_instance=:me, lease_expires_at=:now+lease, last_activity_at=:now WHERE status='running' AND (lease_expires_at <= :now OR owner_instance = :me)`。`rowcount == 0` 说明别人刚刚抢先接管 → 返回 409 `sandbox.session_elsewhere`,**不碰 pty**。这一步保证并发接管只有一个赢家,也保证不会误杀别人的会话;
2. 认领成功后 `provider.attach_shell(runtime_name, provider_pid, cols=, rows=)` 重新拿到输出流,建 state 与读循环,记一条 `shell_session.resumed` 日志。

接管前会先判 `_can_resume`:`provider` 名与当前 provider 匹配、`provider_pid` 非空、provider 实现了 `attach_shell`。任一不满足 → 409 `sandbox.session_lost`,并把行标成 `lost`(这条会话确实回不来了,不该一直吊着 `running`)。

接管失败分两类:`AppError`(provider 明确说没有这个进程)→ `lost`;其他异常(网络抖动等)→ `lost` + 502 `sandbox.provider_error`。两者都标 `lost` 而不是 `running`:避免下次请求对同一进程反复重试。

### 5.4 孤儿回收

持有实例崩溃后,pty 仍活在 provider 侧,登记行仍是 `running`,租约到期后就变成"孤儿"。`sweep` 每轮 `list_stale(limit=50)` 取过期 `running` 行:

1. 跳过本地 state 仍活着且未关闭的 key(它属于自己,续租已经覆盖);
2. 能接管就先 `attach_shell` 再 `shell.close()`——这一步是为了真的把 provider 侧进程杀掉,否则只是把行标掉,pty 会一直烧沙箱资源;
3. `mark_lost(reason="owner lease expired")`;
4. `purge_closed(retention=closed_retention_seconds)` 清掉超过保留期的 `closed` 行。

每轮最多 50 条,避免一次 sweep 打满数据库;积压会在后续轮次继续消化。

## 6. 错误契约

| 场景                           | 错误码                      | HTTP |
| ------------------------------ | --------------------------- | ---- |
| 会话不存在 / 跨账号            | `sandbox.session_not_found` | 404  |
| 会话已关闭                     | `sandbox.session_closed`    | 409  |
| 会话已被别的实例持有且租约有效 | `sandbox.session_elsewhere` | 409  |
| 会话归属行存在但进程不可接管   | `sandbox.session_lost`      | 409  |
| 单沙箱活跃会话超限             | `sandbox.session_limit`     | 409  |
| 单会话订阅者超限               | `sandbox.session_busy`      | 409  |
| 接管时 provider 报错           | `sandbox.provider_error`    | 502  |

`sandbox.session_elsewhere` 的 `details`:`{"session_id", "owner_instance", "provider", "resumable"}`。前端/调用方据此可以提示"这个会话正在另一个实例上运行";`resumable=false` 说明该 provider 不支持接管(如 local),即使等到租约过期也接不回来。

注意 HTTP 错误信封是**扁平**的(`{"code": ..., "message": ..., "details": ...}`),不是嵌套的 error 对象——写测试时容易踩。

由"不存在"到"明确 409"这个变化本身是收益:多副本下再也不会有"会话存在但被当成 404 悄悄吞掉"的情况,排障时能直接看出是实例归属问题。

## 7. provider 接口

`SandboxProvider` 协议新增:

```python
async def attach_shell(
    self, name: str, pid: int, *, cols: int = DEFAULT_SHELL_COLS, rows: int = DEFAULT_SHELL_ROWS
) -> ShellSession: ...
```

- `e2b`:`sandbox.pty.connect(pid, on_data, timeout=0)`(SDK 文档注明可按 pid 连接正在运行的 pty),`timeout=0` 表示不限连接时长;连上后 best-effort `pty.resize`。`E2bShellSession` 暴露 `pid` 属性供登记;
- `local`:抛 `sandbox.provider_unsupported`——宿主 pty 子进程无法被另一个进程接管;
- `microsandbox`:同样抛 `sandbox.provider_unsupported`(SDK 有 `attach`/`attach_shell`,但能否重连既有 tty 流未验证,不做未验证的承诺);
- `sandboxd`:`open_shell` 走 WebSocket PTY(`/v1/sandboxes/{id}/pty`),但沙箱没有可重连的 session id/pid,`attach_shell` 抛 `sandbox.provider_unsupported`;
- `unavailable`:沿用 `_unavailable()`。

协议 docstring 明确要求:不支持重连必须抛 `sandbox.provider_unsupported`,不要返回一个假装能用的会话。

## 8. 恢复的边界

- **输出不回放**:ring buffer 与 `seq` 只在持有实例内存里。接管后 `seq` 从 0 重新计数,接管前的输出不可恢复(SSE 客户端会看到 `seq` 回退)。要真正的"任意实例回放",需要方案 B;
- **只有 e2b 可接管**:生产用 e2b 时重启/滚动升级能接回会话;local、microsandbox 与 sandboxd 只能得到明确的 `session_lost`(sandboxd 能开会话,但没有 pid/重连能力);
- **不是强一致**:租约过期到新实例接管之间最多 120s 窗口,期间请求得到 409 `session_elsewhere` 或(过期后)`session_lost`;
- **不跨 provider**:provider 名不匹配一律 `lost`,不尝试跨 provider 迁移。

## 9. 残余与后续

1. 输出外置(Redis Stream / NATS)以实现真正跨实例的回放与"任意实例可写 stdin";
2. 前端据 `details.resumable` 与 `owner_instance` 做提示与重试指引(当前走通用错误展示);
3. microsandbox 的 `attach` 语义验证后可让本地 provider 也支持接管;
4. 会话指标(活跃数/接管次数/孤儿回收数)接入观测——目前只有日志 `shell_session.resumed`。

## 10. 测试

- `services/workspace-service/tests/test_shell_registry.py`(16 例):登记/查询/续租/原子认领单赢家/回收/实例身份;
- `services/workspace-service/tests/test_shell_takeover.py`(14 例):开会话写登记行、接管后输出继续、活租约 409 且不碰 pty、closed/lost/provider 不匹配/attach 失败、孤儿回收、关闭落库、HTTP 层 409 `sandbox.session_elsewhere`;
- 既有 19 个 shell 会话测试(SSE 帧、心跳、回放、限流)不变;
- workspace-service 全量:267 passed, 1 skipped。
