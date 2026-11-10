# 后台任务工具契约（P1-L / M2：job_start / job_output / job_kill）

状态：**契约定稿（2026-11-10）**，实现待落地（task-16）。上游背景见
[../gap-2026-11/00-refresh-2026-11-09.md](../gap-2026-11/00-refresh-2026-11-09.md) §5.2
与 [tool-execution.md](./tool-execution.md) §9（M2）。

## 1. 目标与非目标

**目标**：让模型在沙箱里启动**长时间**命令（训练、构建、爬取、watch），立刻拿回
`job_id`，之后用 `job_output` 增量/尾部读取输出、用 `job_kill` 终止；
不再被单次工具调用 60~120 秒超时切断，也不必把长任务硬拆成多步 `exec`。

**非目标**：不做交互式终端（PTY 属于 workspace 的终端会话）、不做跨沙箱迁移、
不做 cron/定时调度、不做作业间依赖编排（那是 run 图）、不保证崩溃后进程存活
（沙箱被回收即丢失，状态变 `lost`）。

## 2. 现状与复用

- `exec`（M1）已能在沙箱内 `sh -lc` 直跑并拿真实退出码，但同步阻塞、有超时上限。
- 沙箱提供方：`services/tool-service/tool_service/services/sandbox.py::SandboxProvider`
  （`ensure` / `exec` / `touch` / `aclose`），scope 复用 `conversation:` > `run:` > `account:`。
- 作业目录固定在沙箱工作区：`/workspace/.tool-jobs/<job_id>/`，内含
  `stdout.log`、`stderr.log`、`exit`（退出码文本）、`pid`。
- 作业**元数据落 tool-service 的库**（新增表），进程本身在沙箱里；两者用 `job_id` 关联。
  这样多副本可以各自处理同一批 HTTP 请求（读路径只读库 + 沙箱文件，写路径用条件 UPDATE）。

## 3. 三个工具

```python
JOB_START = ToolDescriptor(
    name="job_start",
    description=(
        "在工作区沙箱内后台启动一条 shell 命令（/bin/sh -lc），立即返回 job_id。"
        "适合训练、构建、长爬取等超过单次 exec 超时的任务；之后用 job_output 查看输出、"
        "job_kill 终止。命令在沙箱内执行且沙箱默认无网络；输出写入作业日志，"
        "建议自行重定向到文件以免日志过大。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "command": {"type": "string", "minLength": 1, "maxLength": 4000},
            "cwd": {"type": "string", "minLength": 1, "maxLength": 512},
            "name": {"type": "string", "minLength": 1, "maxLength": 80},
        },
        "required": ["command"],
        "additionalProperties": False,
    },
    risk=ToolRisk.DESTRUCTIVE, kind=ToolKind.FUNCTION, executor=ExecutorKind.SANDBOX,
    timeout_seconds=30.0, enabled=True,
)

JOB_OUTPUT = ToolDescriptor(
    name="job_output",
    description=(
        "读取后台任务的当前状态与输出尾部（只读）。status=running 表示还在跑，"
        "exited 表示已结束（看 exit_code），killed/timeout 表示被终止，lost 表示沙箱已回收。"
        "wait_seconds>0 时最多等这么多秒直到状态变化或超时。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
            "tail_chars": {"type": "integer", "minimum": 1000, "maximum": 20000, "default": 8000},
            "wait_seconds": {"type": "integer", "minimum": 0, "maximum": 15, "default": 0},
        },
        "required": ["job_id"],
        "additionalProperties": False,
    },
    risk=ToolRisk.READ, kind=ToolKind.FUNCTION, executor=ExecutorKind.SANDBOX,
    timeout_seconds=30.0, enabled=True,
)

JOB_KILL = ToolDescriptor(
    name="job_kill",
    description=(
        "终止一个后台任务（先 SIGTERM 整个进程组，1 秒后 SIGKILL 兜底），日志保留。"
        "任务已结束时不会报错，返回当前状态。"
    ),
    input_schema={
        "type": "object",
        "properties": {"job_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"}},
        "required": ["job_id"],
        "additionalProperties": False,
    },
    risk=ToolRisk.WRITE, kind=ToolKind.FUNCTION, executor=ExecutorKind.SANDBOX,
    timeout_seconds=20.0, enabled=True,
)
```

- `job_start` 是 `DESTRUCTIVE`：run 路径由 plan 闸 + 确认闸保护；对话路径的只读工具集
  不含它（与 `exec` 同口径）。
- `job_kill` 是 `WRITE`：run 路径默认需要确认（风险下限），对话路径不可见。
- `job_output` 是 `READ`：对话路径可见（模型可在对话里查看自己启动的任务；启动仍需 run）。
- 三个工具都受同一开关 `jobs_enabled`（默认 False）控制：关闭时目录/详情/执行三处一致"不存在"。

## 4. 结果信封

`job_start`（ok=true）：

```json
{
  "job_id": "3f2a...（32 位 hex）",
  "status": "running",
  "name": "build",
  "command": "npm run build",
  "cwd": "/workspace",
  "started_at": "2026-11-10T12:00:00Z",
  "log_path": "/workspace/.tool-jobs/<job_id>/",
  "notes": ["长任务请定期 job_output 查看；超过 600 秒会被标记 timeout 并终止"]
}
```

`job_output`（ok=true）：

```json
{
  "job_id": "3f2a...",
  "status": "running | exited | killed | timeout | lost",
  "exit_code": 0,
  "stdout": "尾部文本",
  "stderr": "尾部文本",
  "truncated": true,
  "duration_ms": 12345,
  "started_at": "...",
  "finished_at": "...",
  "notes": ["stdout 只保留最后 8000 字符；完整输出请 read 日志文件"]
}
```

- `status=exited` 时 `exit_code` 为真实退出码（非零也算 ok=true，与 `exec` 同口径）。
- 输出裁剪：单路尾部 `tail_chars`（默认 8000，max 20000），stderr 取 `min(4000, tail_chars//2)`；
  整包 JSON 再压到 `MAX_TOOL_CHARS=25000` 内（复用 `exec_output._fit_exec_result` 的思路）。
- `job_kill`：返回 `{"job_id", "status": "killed"|"exited", "exit_code", "already_finished": bool}`。

## 5. 数据模型与迁移

新表 `background_jobs`（tool-service 自己的库）：

| 列                                    | 类型             | 说明                               |
| ------------------------------------- | ---------------- | ---------------------------------- |
| id                                    | String(32) PK    | 32 位 hex（`uuid4().hex`）         |
| account_id                            | String(64) index | 账号隔离                           |
| sandbox_id                            | String(64)       | 启动时解析出的沙箱                 |
| scope_key                             | String(160)      | 会话/任务/账号 scope，便于审计     |
| run_id / conversation_id              | String(64) NULL  | 来源上下文（可空）                 |
| tool_call_id                          | String(64) NULL  | 启动它的那次工具调用               |
| name                                  | String(80) NULL  | 展示名                             |
| command                               | String(4000)     | 原文（审计只看得到截断）           |
| cwd                                   | String(512)      | 解析后的工作区绝对路径             |
| status                                | String(16) index | running/exited/killed/timeout/lost |
| exit_code                             | Integer NULL     | 退出码                             |
| created_at / updated_at / finished_at | DateTime(tz)     | finished_at 可空                   |

- 索引：`(account_id, created_at)`、`(status)`。
- 迁移：新增 `services/tool-service/migrations/versions/0005_background_jobs.py`，
  `down_revision = "154f533a1698"`（实现前用 `uv run alembic heads` 复核；**不得**改既有迁移）。
  `upgrade` 建表 + 索引，`downgrade` 可逆删除。
- 仓储：`tool_service/repositories/background_jobs.py`（新建）：
  `create` / `get_for_account` / `list_running_count` / `mark_finished`（条件 UPDATE：
  `WHERE id=? AND account_id=? AND status='running'`，rowcount 0 表示已被别人收尾，不覆盖）。

## 6. 执行语义

### 6.1 job_start

1. 校验参数（schema）→ `resolve_exec_cwd`（与 `exec` 同函数，越界 → `tool.invalid_args`）。
2. 配额：`count(status='running' AND account_id=?)` ≥ `job_max_concurrent_per_account`（默认 5）
   → `tool.budget_exceeded`（retryable false，hint 先 job_kill 或等结束）。
3. `ensure` 沙箱（scope 与 `exec` 同：`scope_key_for(...)`）。
4. 清理（best-effort，失败只 warning）：作业目录里 mtime 超过 `job_retention_hours`（默认 24）
   的子目录 `rm -rf`；库里同账号超过保留期的终态行删除。
5. 生成 `job_id` 与作业目录，单次 `sandbox.exec` 启动后台进程（脚本见 §6.4）。
6. 写库（status=running）→ 返回信封。**先启动再落库**：落库失败时尽力 kill 刚启动的进程，
   返回 `tool.upstream_error`。

### 6.2 job_output

1. 查库（`id + account_id`，查不到 → `tool.not_found`，不泄漏存在性）。
2. `status` 已是终态 → 直接读日志尾部 + 库字段返回（`lost` 不读沙箱）。
3. `running`：若 `now - created_at > job_max_runtime_seconds`（默认 600）→ 走 **lazy timeout**：
   kill 进程组 → 库置 `timeout`/`finished_at` → 返回 `status=timeout`。
4. 单次 `sandbox.exec` 读状态与日志（§6.5），解析出 `exit_code` / `status`：
   - exit 文件存在 → 库条件 UPDATE 为 `exited` + exit_code + finished_at；
   - 沙箱 `ensure/exec` 报 404/不可用 → 库置 `lost`（best-effort）并返回 `lost`；
   - 其余异常 → `tool.upstream_error`（不改变库状态）。
5. `wait_seconds>0`：在 [0, wait_seconds] 内每 1 秒重读一次，状态变化即返回；超时返回当前状态。
6. 返回信封（§4）。

### 6.3 job_kill

1. 查库 + 账号隔离（同 job_output）。
2. 终态 → 直接返回 `already_finished=true` + 当前状态与 exit_code。
3. running → 单次 `sandbox.exec` 执行终止脚本（§6.6）→ 库条件 UPDATE 为 `killed` + finished_at
   （若 kill 前恰好已退出，则以 exit 文件为准置 `exited`）。
4. 沙箱不可用 → 库置 `lost`，返回 ok=true + `status=lost`（幂等：任务已不可能继续）。

### 6.4 job_start 启动脚本（argv 只有 `/bin/sh -lc <script>`）

- 服务端生成（`shlex.quote` 只对 `command` 用一次；目录/名称由服务端拼装、不含用户输入）：
  ```sh
  mkdir -p <dir> && cd <cwd> && \
  ( <command> ) > <dir>/stdout.log 2> <dir>/stderr.log < /dev/null & \
  echo $! > <dir>/pid
  ```
  实际实现必须保证"退出码落盘"：把 `command` 包进内层脚本
  `/bin/sh -lc '<单引号转义后的 command>' >stdout.log 2>stderr.log; echo $? >exit`，
  该内层脚本再后台化（`setsid` 存在则用，否则 `nohup`），`pid` 写进程组 leader。
  **约束**：模型输入只经 `shlex.quote` 拼一次，不得字符串拼接未转义内容；`cwd` 已由
  `resolve_exec_cwd` 归一为 `/workspace` 内绝对路径。
- 启动完成判据：`exec` 返回后 `pid` 文件必须存在；不存在视为启动失败 `tool.upstream_error`。

### 6.5 job_output 状态/日志读取脚本（单次 exec）

```sh
if [ -f <dir>/exit ]; then printf 'EXIT:'; cat <dir>/exit; else printf 'RUNNING'; fi
printf '\n---STDOUT---\n'; tail -c <stdout_chars> <dir>/stdout.log 2>/dev/null
printf '\n---STDERR---\n'; tail -c <stderr_chars> <dir>/stderr.log 2>/dev/null
```

- 解析 `EXIT:<n>` / `RUNNING`；日志文本按标记切分。
- 若 pid 文件存在但进程已不存在且无 exit 文件（被 OOM/kill -9 打死）→ 判 `lost`，
  hint "进程已消失且未写退出码，可能是沙箱 OOM 或外部终止"，库置 `lost`。
  判定方式：脚本里 `kill -0 $(cat pid)` 失败即 `NOEXIT`。

### 6.6 job_kill 脚本

```sh
pid=$(cat <dir>/pid 2>/dev/null || true)
if [ -z "$pid" ]; then echo MISSING; exit 0; fi
kill -TERM -"$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
sleep 1
kill -KILL -"$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
echo KILLED
```

- 单个工具调用的整体超时仍是 descriptor 的 20~30 秒；沙箱 exec 超时映射
  `tool.timeout`（kill 本身已尽力完成）。

## 7. 配置（`tool_service/core/config.py`）

```python
jobs_enabled: bool = False                   # NETWORK_SWITCHES 口径，测试钉 False
job_max_runtime_seconds: int = 600           # lazy 超时上限
job_max_concurrent_per_account: int = 5
job_output_default_chars: int = 8000
job_output_max_chars: int = 20000
job_retention_hours: int = 24
```

- `arguments.py::is_available`：`job_start` / `job_output` / `job_kill` → `settings.jobs_enabled`。
- `main.py` 装配：`ToolRunner` 需要仓储工厂（`session_scope`）；仓储不提交，事务边界由
  runner 的 service 层用 `session_scope` 控制（沿用既有 tool-service 执行审计写法）。
- **打开 jobs_enabled 的副作用**：`job_start` 是 DESTRUCTIVE，orchestrator 的 plan 闸会对
  所有 run 触发计划确认（与 `exec`/`run_code` 同款）。文档与配置注释必须写清。

## 8. 错误码

| 场景                     | code                   | retryable |
| ------------------------ | ---------------------- | --------- |
| 参数/cwd 非法            | `tool.invalid_args`    | false     |
| 作业不存在/已过期/跨账号 | `tool.not_found`       | false     |
| 并发超限                 | `tool.budget_exceeded` | false     |
| 沙箱不可用               | `tool.upstream_error`  | true      |
| 沙箱调用超时             | `tool.timeout`         | true      |
| 启动后 pid 缺失          | `tool.upstream_error`  | true      |

## 9. 测试口径（零网络，FakeSandboxProvider 脚本化）

1. 目录：三工具 descriptor、risk（DESTRUCTIVE/READ/WRITE）、schema 校验（job_id 格式）。
2. 开关：`jobs_enabled=false` → 目录不出现、执行 404 `tool.not_found`。
3. `job_start`：断言 `sandbox.exec` 的 argv 是 `["/bin/sh","-lc",script]`、脚本里 command 被
   单引号安全引用（含 `'` 与 `$(...)` 的恶意样例不逃逸）、cwd 解析正确、落库 status=running、
   返回 32 位 job_id。
4. 并发上限：库里预置 5 条 running → `tool.budget_exceeded`。
5. `job_output`：running（无 exit）、exited（exit=0 / 非零）、lost（pid 消失无 exit）、
   沙箱 404 → lost、日志尾部截断与 notes。
6. `job_kill`：running → killed、已 exited → already_finished、跨账号 → not_found。
7. lazy timeout：created_at 回拨 601 秒 → 返回 timeout 且库状态更新。
8. 迁移：`test_migrations.py` 口径补 `background_jobs` 表存在性；alembic upgrade/downgrade 可逆。
9. 审计：job_start/job_output/job_kill 各产生一行 tool_calls（沿用既有 execution audit 测试范式）。

## 10. 残余风险

- 作业是**沙箱内进程**，沙箱被 reaper 回收或 OOM 即丢失（`lost`），不保证 at-least-once。
- lazy 超时只在下一次 job_output/job_kill 时触发；无 sweeper，长期无人查看的任务会占用配额
  直到有人访问或沙箱回收。
- 日志只保留尾部；完整输出需模型自己重定向到工作区文件再用 read 读。
- 多副本下同一 job 的两个 job_kill 并发：条件 UPDATE 保证只有一个收尾，重复 kill 幂等。
- 进程组终止依赖 `setsid`；沙箱镜像无 `setsid` 时降级为只杀直接子进程（孙进程可能残留），
  契约允许并需在 `notes`/日志留痕。
- 无 Web 端专用面板：本期用通用工具调用卡片展示（前端改造留到前端批次）。
