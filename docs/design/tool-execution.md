# 工具执行面设计:exec / web_search / todo(P1-L)

> 状态:**exec MVP(2026-10-27)与 todo 第一轮(2026-10-27)已落地**;web_search 仅为契约与分期,未实施。范围:exec 在 `services/tool-service`(契约/执行/审批/审计),orchestrator 侧不加新代码(危险工具复用既有审批链);todo 在 `services/agent-orchestrator`(进程内工具 + run 快照 + 事件 + 提示注入 + 只读 API,不进 tool-service 目录)。
> 关联:`docs/gap-2026-10/08-refresh-2026-10-27.md` 的 P1-L 与能力矩阵「工具面(执行/发现)」;`docs/design/tool-service.md`(D13 修订注记);`docs/design/context-engineering.md`(大工具结果 offload)。

## 1. 摘要

云端 run 此前只能读/写/改文件,**不能执行命令**:「改完跑测试」的闭环缺失(桌面本地模式已有 `run_command`,云端没有等价能力)。本册把「跨主流 agent 的必需执行面」拆成三件,并只落第一件:

- **exec(已落地)**:`tool-service` 目录新增一等工具,在**工作区沙箱内**执行一条 shell 命令。风险档 `destructive`(legacy `danger`),默认关闭,开启后必须人工确认;输出自带截断与回读提示。
- **web_search(待实施)**:联网检索(与只读抓取的 `fetch` 不同,检索需要搜索提供方与引用协议);依赖出口策略,见 §11。
- **todo(已落地,第一轮)**:`todo_write` 是 **orchestrator 进程内**的 run 级工具(不进 tool-service 目录):模型把任务清单整包覆盖写进 `runs.todos` 快照、落 `run.todo.updated.v1` 事件,并在每轮 execute/verify/deliver 的系统提示里注入极简清单;只读端点 `GET /api/v1/runs/{id}/todos`,前端在 run 详情与会话右栏显示「任务清单」。依赖 P1-D 的 item 化不在本期(差异见 §13.4)。

五条关键决策:

1. **exec 是目录一等公民**,不是 orchestrator 的私有能力:执行、限额、审计、幂等、租户隔离继续由 `tool-service` 统一承担,orchestrator 无需改代码。
2. **默认关闭(`TOOL__EXEC_ENABLED=false`)**:`destructive` 工具一旦出现在启用目录里,orchestrator 的 plan 闸会对**所有** run 触发人工确认(`_has_dangerous_tools`)。默认关闭保证既有部署行为零变化。
3. **执行协议与文件工具分家**:`read`/`write` 等走沙箱内 helper 脚本(stdin 传 JSON 请求、返回 JSON 信封、路径 jail 在 helper 内);`exec` 需要真实 argv、cwd 与退出码,直接调 `SandboxProvider.exec`,不经 helper。
4. **结果不撞 25K 上限**:exec 自行截断 stdout(16,000 字符)/stderr(8,000 字符),再按整包 JSON 体积回缩到 `MAX_TOOL_CHARS`(25,000)以内,并提示「重定向到文件后用 read 读」。
5. **复核 D13**:`tool-service` 设计稿原决定「不引入任意命令执行、不开沙箱联网」;现在沙箱(microVM + `network=deny`)提供了执行边界,缺的是**受控暴露**。本册修订 D13 的适用范围:文件 helper 仍然不执行命令,exec 是独立受控通道(默认关 + 确认 + 审计)。

## 2. 现状(证据)

| 能力         | 落地前                                                                                | 位置                                                                                                  |
| ------------ | ------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 文件工具     | 已有:`read`/`glob`/`grep`/`list_dir`/`write`/`edit`,全部在沙箱内、路径 jail 在 helper | `services/tool-service/tool_service/services/catalog.py`                                              |
| 命令执行     | 缺失:目录 9 个工具里没有 exec,也没有 shell                                            | 同上 `TOOLS`                                                                                          |
| 内部 exec    | 有,但只服务内部逻辑:上下文文件发现、技能执行                                          | `services/agent-orchestrator/agent_orchestrator/context/sandbox_files.py`、`services/skill_runner.py` |
| 桌面本地模式 | 有 `run_command`(工作区根目录、默认 120s、>256KB 截断)                                | `apps/desktop/src/main/local/agent.ts`                                                                |
| 沙箱隔离     | microVM(microsandbox)默认,`sandbox_network=deny`,exec 默认 60s、256KB 输出上限        | `services/workspace-service/workspace/services/sandbox_provider.py`                                   |
| 结果上限     | 超过 25,000 字符直接回 `tool.too_large`(模型拿不到更大结果)                           | `services/tool-service/tool_service/constants.py`                                                     |

D13 原文(需修订的决策)在 `docs/design/tool-service.md` §决策表:「不引入任意命令执行、不开沙箱联网」。当时的理由是没有可用的隔离执行面;**当前沙箱已经具备**(microVM + 网络 deny + 输出/超时上限),因此改为「默认关闭、按部署开启、逐次确认」。

## 3. 目标与非目标

### 3.1 目标

1. 开启后,一个 run 能完成「改文件 → 跑测试/构建 → 读失败原因 → 再改」闭环,不再依赖人肉在本地跑命令。
2. 输出可控可读:超长输出不会报错也不会撑爆上下文,有明确的回读路径(重定向到文件 + `read`)。
3. 危险边界可控:默认不暴露、逐次确认、沙箱隔离、`tool_calls` 可审计。
4. 关闭时行为与改造前完全一致(目录、执行、审批链路都不变)。

### 3.2 非目标

- 不开沙箱网络(`network=deny` 不变):`pip install` / `npm install` 之类需要 egress 的命令会失败,这是有意的边界。
- 不做后台任务 / 持久 shell 会话 / PTY(二期,见 §11)。
- 不做命令前缀白名单/自动放行(依赖 P1-A policy 引擎);本册所有 exec 调用都要确认。
- 不在宿主机执行任何命令;`LocalSandboxProvider`(无隔离)只允许本地单租户开发。
- 不替换文件工具:读文件优先 `read`(有 sha/行号/读前置),exec 只用于「必须有进程」的场景。

## 4. 与主流对照

| 方案        | 执行工具形态                                   | 审批/隔离             | 我们的取舍                                  |
| ----------- | ---------------------------------------------- | --------------------- | ------------------------------------------- |
| Claude Code | `Bash`(持久 shell 会话)+ sandbox 模式          | 权限规则 + 每命令确认 | 一期一次性 `sh -lc`,无持久会话              |
| Codex CLI   | `exec_command` + `write_stdin` + `apply_patch` | 沙箱模式 × 审批策略   | 对齐一次性执行;`apply_patch` 用 `edit` 替代 |
| Gemini CLI  | `run_shell_command`                            | 确认 + 超时           | 对齐(确认 + 超时)                           |
| Cursor      | `run_terminal_cmd`                             | 持久终端、用户可见    | 二期再考虑持久会话                          |
| OpenHands   | `BashTool`(tmux 持久会话)                      | 容器隔离              | 二期                                        |
| Deep Agents | `execute`(`sh -lc`)                            | 沙箱后端              | **对齐**(同形态)                            |
| AgentScope  | `execute_shell_command`                        | 容器/本地             | 对齐                                        |
| DSH         | `bash` + `job_start`/`job_output`/`job_kill`   | 沙箱 + 审批           | 一期只做 `bash` 等价物,后台任务二期         |

结论:主流都把**一次性命令执行**当作最小执行面,持久会话与后台任务是增速项而非必需项。我们在「确认 + 沙箱 + 截断」上对齐,在「持久会话」上明确延后。

## 5. exec 契约

| 字段            | 值                             |
| --------------- | ------------------------------ |
| name            | `exec`                         |
| risk            | `destructive`(legacy `danger`) |
| kind / executor | `function` / `sandbox`         |
| timeout_seconds | 60(descriptor 默认上限)        |

参数:

| 参数              | 类型    | 约束              | 说明                                                                                     |
| ----------------- | ------- | ----------------- | ---------------------------------------------------------------------------------------- |
| `command`         | string  | 必填,1..4000 字符 | 交给 `/bin/sh -lc` 的完整命令;可含管道/重定向/`&&`                                       |
| `cwd`             | string  | 可选,≤512 字符    | 相对 `/workspace` 的工作目录,默认 `/workspace`;绝对路径也接受,但必须落在 `/workspace` 内 |
| `timeout_seconds` | integer | 可选,1..120       | 本次命令超时(秒);省略时用 descriptor 默认 60,显式给出时按 1..120 夹取后生效              |

### 5.1 成功信封

命令跑完即 `ok=true`,**非零退出码不是执行失败**(失败信息在 stderr,模型需要它才能修):

| 字段                | 说明                                                              |
| ------------------- | ----------------------------------------------------------------- |
| `exit_code`         | 进程退出码;`124` 表示被超时终止,`126/127` 表示不可执行/找不到命令 |
| `stdout` / `stderr` | 文本输出(已截断)                                                  |
| `truncated`         | 任一输出被截断                                                    |
| `duration_ms`       | 沙箱侧执行耗时                                                    |
| `cwd`               | 实际生效的绝对工作目录                                            |
| `notes`             | 截断/回读提示(如「重定向到文件后用 read 读取」)                   |

### 5.2 失败信封

| code                         | 场景                                                            | retryable |
| ---------------------------- | --------------------------------------------------------------- | --------- |
| `tool.invalid_args`          | 参数不合法、`cwd` 逃出 `/workspace`                             | false     |
| `tool.timeout`               | 沙箱获取/命令执行超时(下游 `sandbox.exec_timeout` 也映射到这里) | true      |
| `tool.upstream_error`        | 沙箱不可用/连接失败/下游 5xx                                    | true      |
| `tool.not_found`             | exec 未开启(API 404,能力即权限边界)                             | false     |
| `tool.confirmation_required` | 未带 `approved=true`(API 403)                                   | false     |
| `tool.budget_exceeded`       | 单 run 工具调用次数超限                                         | false     |

### 5.3 截断与体积

- stdout 超过 **16,000** 字符 → 截断并置 `truncated=true`,附回读提示。
- stderr 超过 **8,000** 字符 → 同上(错误栈前段通常足够定位)。
- 截断后再按整包 JSON 体积回缩:必须 ≤ `MAX_TOOL_CHARS`(25,000),否则等比缩短两路输出并补 note。**exec 不会回 `tool.too_large`**。
- 输出是纯文本,不做 base64/图片;二进制产物应在沙箱内落盘,再用 `read`(文本)或交付物通道取回。

## 6. 执行路径

```text
orchestrator 工具调用
  → POST /v1/tools/exec:execute
      → 目录查找 + is_available(TOOL__EXEC_ENABLED)
      → 幂等重放检查(tool_call_id)
      → destructive 且未 approved → 403 tool.confirmation_required
      → 单 run 调用次数配额
      → ToolRunner.execute → _run_exec
          → 参数校验 → cwd 夹取 → sandbox.ensure → sandbox.exec(/bin/sh -lc command, cwd)
          → 截断/回缩 → 成功信封 → touch(TTL 续期)
      → tool_calls 审计落库
```

`_run_exec` 的关键点:

1. **不进 helper**:`helpers/fs_helper.py` 的协议是「stdin 传 JSON 请求 + 返回 JSON 信封 + 路径 jail」,塞进命令执行会多一层且拿不到真实退出码/信号;exec 直接调用 `SandboxProvider.exec`。
2. **argv 固定 `[/bin/sh, -lc, command]`**:与主流 `sh -lc` 语义一致(登录 shell 的简化版),支持管道与重定向;命令字符串长度由 schema 限制。
3. **cwd 夹取**:相对路径按 `/workspace` 解析,`posixpath.normpath` 归一后必须以 `/workspace` 开头,否则回 `tool.invalid_args`。注意这**不是**安全边界(shell 内仍可 `cd /et`),真正的边界是沙箱。
4. **超时**:请求带 `timeout_seconds` 时按 1..120 夹取后直接使用;不带时回退 `_timeout_seconds()`(descriptor 的 60s 优先,ExecutionContext 带超时时再取小);沙箱侧另有 `sandbox_exec_timeout_seconds`(默认 60)兜底。
5. **TTL 续期**:与文件工具一致,执行后 `touch`,失败不影响结果。

## 7. 审批与安全

### 7.1 风险档为什么是 destructive

shell 能绕过文件工具的路径 jail(直接 `cat /workspace/../etc/passwd` 之类,虽然沙箱内仍有边界)、能读进程环境、能起长期进程、能写任意沙箱内文件、能在沙箱内发起网络请求(策略上 deny)。风险等价于**任意代码执行**,而不是普通写入,因此按 `destructive`(legacy `danger`)对待。

### 7.2 审批链

1. **orchestrator plan 闸**:启用目录里存在危险工具时,`approval_gate_node` 一定 `interrupt`(kind=plan),人工确认后才执行计划。
2. **confirm 闸**:`RiskDefaultApproval` 对 `danger` 返回 CONFIRM → `interrupt`(kind=confirmation),批量 `writes_approved` 后以 `approved=true` 执行。
3. **tool-service 兜底**:`execute` 与 `ToolRunner` 都做「WRITE/DESTRUCTIVE 无 approved → 403」,防越权直连。
4. **目录开关**:`TOOL__EXEC_ENABLED=false` 时 list/get/execute 一致地视为不存在(404),不会有「目录没有但能执行」的缝。

### 7.3 打开 exec 的部署前提

- 沙箱必须用 `sandbox_provider=microsandbox`(microVM);`local` provider 无隔离,只允许本地单租户开发。
- `sandbox_network=deny` 保持默认;**不要**为了 `pip install` 打开网络。
- 单 run 配额(`TOOL__MAX_TOOL_CALLS_PER_RUN`)与沙箱 TTL 是唯一的时间/次数兜底,生产建议调低。
- 需要人工确认 UI(orchestrator 的 confirmation 闸);无人值守的自动化 run 不应开启 exec。

### 7.4 已知风险与缓解

| 风险                        | 缓解                                                                       |
| --------------------------- | -------------------------------------------------------------------------- |
| 资源耗尽(CPU/内存/长跑进程) | 沙箱 cpus/memory 上限 + exec 超时 + microVM 硬隔离(二期加 run 级 CPU 预算) |
| 沙箱内删掉交付物/改坏仓库   | 确认闸 + `git` 可回滚(工作区是租户文件真源,不在容器外)                     |
| 输出洪水                    | 16K/8K 截断 + 25K 硬上限 + note 提示回读                                   |
| 沙箱内 exfil(网络)          | `network=deny`;不开放 egress,`fetch` 与 exec 互不替代                      |
| 供应链(沙箱内装依赖)        | 默认失败;需要预装依赖应在镜像/工作区准备阶段完成                           |
| microVM 逃逸                | 依赖 microsandbox 上游;紧跟安全公告,不以 exec 为信任边界                   |
| 命令注入                    | 命令本身就是模型给的字符串,不经拼接;审批与沙箱是唯一防线                   |

### 7.5 与 P1-A 的关系

P1-A policy 引擎落地后,exec 可以按「命令前缀 allowlist」(如 `pytest`/`ruff`/`npm test`)自动放行,其余仍需确认;本册先保留全量确认,不做静态规则。

## 8. 审计与事件

- **审计行**:`tool_calls` 记录 `tool_name=exec`、`risk=destructive`、`decision=approved`、`arguments`(命令行,截 2000 字符)、`duration_ms`、`result_chars`、`sandbox_id`、`ok/error_code`、`truncated`。`exit_code` 在结果里,不新增数据库列(不改迁移)。
- **事件**:`run.tool.called.v1` 由 orchestrator 从审计行增量生成,`_tool_event_payload` 白名单会带 arguments/output 截断;offload 字段沿用 P1-F。
- **可观测**:前端 `/runs` 详情抽屉已有工具调用列表,命令行直接在 arguments 里可见,不需要新 UI。

## 9. 配置

| 变量                             | 默认    | 说明                                   |
| -------------------------------- | ------- | -------------------------------------- |
| `TOOL__EXEC_ENABLED`             | `false` | 开启 `exec`;关闭时目录不暴露、执行 404 |
| descriptor `timeout_seconds`     | `60`    | 单条命令默认上限(请求可降到 1..120)    |
| `TOOL__TOOL_TIMEOUT_SECONDS`     | `30`    | 全局默认(exec descriptor 覆盖为 60)    |
| `TOOL__MAX_TOOL_CALLS_PER_RUN`   | `60`    | 单 run 工具调用次数上限(含 exec)       |
| `TOOL__SANDBOX_TOOL_TTL_SECONDS` | `1800`  | 沙箱滑动 TTL                           |

orchestrator 侧无新配置:危险工具经目录自动进入审批链。

## 10. 测试计划

| 用例组     | 断言                                                                                    |
| ---------- | --------------------------------------------------------------------------------------- |
| 目录与开关 | 默认:list 不含 exec、GET 404、execute 404;开启:出现且 risk/executor 正确                |
| 执行成功   | argv 为 `[/bin/sh, -lc, command]`、cwd 默认 `/workspace`、`stdin` 为 None、信封字段齐全 |
| 非零退出   | `ok=true`、`exit_code` 原样、stderr 可读                                                |
| 截断       | stdout 20K → 16K、`truncated=true`、notes 有回读提示                                    |
| 体积回缩   | 极端换行输出下整包 JSON ≤ 25,000                                                        |
| cwd 夹取   | 相对路径归一、`..` 逃逸与 `/etc` 拒绝、`/workspace` 前缀比较不误伤 `/workspace2`        |
| 超时       | 请求 `timeout_seconds` 生效;下游 `sandbox.exec_timeout` → `tool.timeout`                |
| 上游故障   | `tool.upstream_error`、retryable=true                                                   |
| 审批       | 无 `approved` → 403 且不触达沙箱                                                        |
| 审计       | `tool_calls` 行 decision=approved、arguments 含命令行                                   |
| 零网络     | 全部打桩 `FakeSandboxProvider`,不发真实请求                                             |

## 11. 分期与验收

| 期  | 内容                                                           | 状态                                  |
| --- | -------------------------------------------------------------- | ------------------------------------- |
| M1  | exec MVP:目录/开关/执行/审批/截断/审计/测试                    | **已落地(2026-10-27)**                |
| M2  | 后台任务(`job_start`/`job_output`/`job_kill`)+ 持久 shell 会话 | 待启动(可复用 workspace 的 PTY 会话)  |
| M3  | `web_search`:搜索提供方 + 引用协议 + 出口策略                  | 待启动(先定提供方与成本口径)          |
| M4  | `todo`:run 级任务清单(第一轮)                                  | **已落地(2026-10-27)**;item 化待 P1-D |

M1 验收:

1. 开启 exec 的 run 能完成「改文件 → 跑测试 → 读失败 → 再改」闭环,命令与输出在 `/runs` 可见。
2. 关闭 exec 时,目录、执行、审批链路与改造前完全一致。
3. `tool-service` 全量测试通过,覆盖率不降(门禁 82)。

## 12. 实施记录

### 12.1 文件清单

| 文件                                                       | 改动                                                                                    |
| ---------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `services/tool-service/tool_service/services/catalog.py`   | 新增 `EXEC` 描述符(10 个工具);`CATALOG_VERSION` 由 `2026-10-25.p4` 升至 `2026-10-27.p5` |
| `services/tool-service/tool_service/services/execution.py` | `is_available()` 下放到执行层;`_run_exec()`;cwd 夹取;截断/回缩                          |
| `services/tool-service/tool_service/services/sandbox.py`   | `SandboxProvider.exec` 增加 `cwd`;HTTP 实现透传                                         |
| `services/tool-service/tool_service/core/config.py`        | `exec_enabled: bool = False`                                                            |
| `services/tool-service/tool_service/constants.py`          | exec 输出/命令/超时常量                                                                 |
| `services/tool-service/tool_service/api/v1/tools.py`       | 复用 `is_available`;execute 也做可用性检查(404)                                         |
| `services/tool-service/tests/`                             | 新增 `test_tool_exec.py`;替身 `exec` 增加 cwd;目录/可用性用例更新                       |

### 12.2 与本文的差异

- MVP 未做「命令前缀 allowlist」(属 P1-A)。
- `stdout` 截断实际为「先 16K/8K 截断,再按整包 25K 回缩」,与 §5.3 一致。
- `fetch` 关闭时的执行行为从 `tool.executor_unavailable`(HTTP 执行器缺失)统一为 404 `tool.not_found`(可用性检查前移),行为更强一致。

### 12.3 验证

- `services/tool-service`:`uv run pytest -q -p no:randomly` → **298 passed**,覆盖率 **88.15%**(门禁 82);`ruff check` / `ruff format --check` 全绿;全部打桩 `FakeSandboxProvider`,零网络。
- 定向:`tests/test_tool_exec.py` 31 例(描述符/开关/审批/argv/cwd 夹取/截断与体积回缩/超时映射/审计/参数化 cwd 12 例)。
- **orchestrator 未改代码**:`RemoteToolkit.risk()` 把 `risk=destructive` 经 `packages/common/common/tools.py:65` 映射为 legacy `danger` → `has_dangerous` 为真时 plan 闸自动 interrupt;confirm 闸对 `danger` 回 CONFIRM;chat 只读路径按 `risk()=="read"` 裁剪(`services/conversations_persist.py:245-259`),exec 在对话里不可见。
- 文档:`services/tool-service/README.md`(工具清单/exec 小节/配置)、`.env.example` 的 `TOOL__EXEC_ENABLED=false`、`docs/design/tool-service.md` 的 D13 修订注记与 P4 分期行、`docs/gap-2026-10/08-refresh-2026-10-27.md` 的 P1-L 与能力矩阵新增行。

### 12.4 已知残留(未在本期解决)

- **chat 路径用不到 exec**:对话侧只保留 `risk=read`,而 exec 是 `destructive`;想让对话助手跑命令需 P1-A policy 做命令前缀 allowlist 后再单独开 READ 档通道。
- **无持久 shell/后台任务**:每次调用一条 `sh -lc`,`cd`/环境变量/后台进程都不跨调用保存(M2)。
- **输出仍受 `MAX_TOOL_CHARS` 约束**:exec 自行回缩到 25K 以内,与 P1-F 的 offload 没有串联(offload 目前只覆盖 orchestrator 侧工具结果信封;exec 超长输出应「重定向到文件 → read」)。

## 13. todo 实施记录(M4 第一轮,2026-10-27)

### 13.1 位置:orchestrator 进程内,不进 tool-service 目录

- tool-service 是无状态执行器:不知道 run 上下文、graph state 与 run 事件;`todo_write` 要写 run 快照、发事件、把清单注入图内提示,这些只在 orchestrator 侧成立。
- 同层先例:`services/memory_tools.py`、`context/skills.py` 的 `load_skill` 都是进程内工具;挂载点在 `core/container_assemblies.py` 的 `ctx.run_id` gate,chat 路径不暴露(对话没有 run,也没有地方存清单)。
- 目录是「能力/权限边界」,描述沙箱与外部副作用;todo 是 run 内状态、不是沙箱能力,因此不占 `CATALOG_VERSION` 版本位,也不落 tool-service 的 `tool_calls` 审计表(清单本身就是状态流水)。

### 13.2 契约

| 项       | 值                                                                                |
| -------- | --------------------------------------------------------------------------------- |
| 工具名   | `todo_write`(`risk=write`、`kind=function`)                                       |
| 参数     | `todos`:数组,必填,1..`todo_max_items`(20)项                                       |
| 单项     | `content`:去首尾空白后 1..200 字符;`status`:`pending`/`in_progress`/`completed`   |
| 约束     | 同时最多一项 `in_progress`;超项数/超长/未知状态直接拒绝(不落库)                   |
| 写语义   | 覆盖写全量(没有 add/done/remove 之类的增量工具)                                   |
| 幂等     | 与当前清单逐字相同 -> `updated=false`,不写库、不落事件                            |
| 成功信封 | `{ok, updated, todos, counts:{pending,in_progress,completed}, revision, summary}` |
| 失败     | `{"error": ...}`(参数/上下文/租户错误,`tool.invalid_args` 口径)                   |

为什么覆盖写:模型每轮都能看到整份清单(§13.4),增量动作会让「当前清单」依赖一串历史动作,回放与幂等成本更高;20 项上限让整包写的代价可控。

### 13.3 数据与事件

- `runs.todos`:`JSON NOT NULL DEFAULT '[]'` 快照列(迁移 `b9e4d1f7c2a8`,接在 `b7d1e9c3a5f2` 之后);读路径宽松清洗,坏数据不 500。
- `run.todo.updated.v1`:`{todos, counts, revision, source:"tool"}`;`revision = 该 run 同类事件数 + 1`。
- 为什么「快照 + 最新事件 revision」而不是纯事件投影:todo 是覆盖写的最新状态,投影要回放全部事件才能得到当前清单;快照一次读即可,事件补充 revision 与流水。P1-D 落地后把该事件升级为 `item`(kind=todo),列与端点不用改。

### 13.4 提示注入

- 每轮重建 `_execute_messages` 时按 state 追加最新清单;`verify`/`deliver` 也注入(只读),让交付前收尾看得到未完成项。**不**写进 `context_block` 快照(那是起图时冻结的 run 级上下文,清单每轮会变)。
- 格式极简、非 JSON:

  ```text
  当前任务清单(2/3 完成):
  - [x] 读需求文档
  - [~] 改实现
  - [ ] 跑测试
  ```

- 替换式注入:历史消息里不累积清单块,只有系统提示携带最新一份。
- resume/retry:services 层从 `runs.todos` 重新装载进 state;旧 checkpoint 没有该字段时按无清单处理。
- 工具描述写明纪律:开工前落 3-8 项、开始某项前设 `in_progress`、同时最多一项、做完立刻 `completed`、每次传完整清单。

### 13.5 API 与前端

- `GET /api/v1/runs/{run_id}/todos` -> `{run_id, todos, counts, revision}`;租户隔离走 `require_run`(跨租户 404)。不内联进 `RunRead`,避免 run 列表每次带清单。
- 前端:`apps/web/src/lib/runs-api.ts` 的 `runsApi.todos`;`apps/web/src/components/console/run-todo-list.tsx` 渲染「任务清单」块;run 详情执行中 1.5s 轮询、终态停止;会话右栏只在有 `activeRunId` 时请求;空清单整块不渲染。

### 13.6 文件清单

| 文件                                              | 改动                                                            |
| ------------------------------------------------- | --------------------------------------------------------------- |
| `agent_orchestrator/graph/todos.py`               | 新增:常量/计数/清洗/校验/渲染(graph 层单一来源,不依赖 services) |
| `agent_orchestrator/services/todos.py`            | 新增:`todo_write` 装配 + `list_todos` 读端 + 事件常量           |
| `migrations/versions/b9e4d1f7c2a8_run_todos.py`   | 新增:`runs.todos` JSON 列(server_default `'[]'`)                |
| `agent_orchestrator/models/runs.py`               | `todos` 列                                                      |
| `agent_orchestrator/graph/state.py`               | `todos` state 字段                                              |
| `agent_orchestrator/graph/plan_nodes.py`          | `_todo_block()`                                                 |
| `agent_orchestrator/graph/nodes.py`               | execute/tool_exec 注入 + 工具结果回写 state                     |
| `agent_orchestrator/graph/deliver_nodes.py`       | verify/deliver 注入                                             |
| `agent_orchestrator/services/run_execution.py`    | 起图时从 `runs.todos` 装载 state                                |
| `agent_orchestrator/core/config.py`               | `todo_enabled`/`todo_max_items`/`todo_max_content_chars`        |
| `agent_orchestrator/core/container_assemblies.py` | run 上下文挂载 `todo_write`                                     |
| `agent_orchestrator/schemas/runs.py`              | `TodoItem`/`TodoCounts`/`TodoRead`                              |
| `agent_orchestrator/services/runs.py`             | 导出 `list_todos`                                               |
| `agent_orchestrator/api/v1/runs.py`               | `GET /{run_id}/todos`                                           |
| `apps/web/src/lib/runs-api.ts` 等 8 个前端文件    | 类型/query key/清单组件/详情与会话接线/测试                     |

### 13.7 验证

- `npx nx test agent-orchestrator`:**858 passed**,覆盖率 **87.1%**(本次 87.11%,门禁 82)。
- 定向新增:`tests/test_todos.py` 26 例(纯逻辑/工具/读端/图注入)+ `tests/test_todo_graph.py` 4 例(节点回写 state、失败信封不覆盖、提示词带清单)+ `tests/test_todo_api.py` 6 例(API/租户隔离/开关/挂载 gate),共 36 例,全部零网络。
- 迁移:临时 sqlite 上 `alembic upgrade head` 到 `b9e4d1f7c2a8`,`alembic check` 输出 `No new upgrade operations detected.`。
- OpenAPI 契约快照 `tests/snapshots/openapi.json` 已更新(新增 `GET /runs/{run_id}/todos`,`TodoItem`/`TodoCounts`/`TodoRead` schema)。
- 既有 6 个断言工具面的测试补上 `todo_write`(`test_container_context`/`test_skill_runner_extras`/`test_knowledge_tools`/`test_tools_providers`/`test_mcp_references`);前端 `npx nx test @beauty/web -- --run` 67 files / 462 tests 通过,`typecheck` 与 `lint` 干净。
- 文档:本文 §1/§11/§13、`docs/design/tool-service.md` 的「todo 本期不做」修订注记、`docs/gap-2026-10/08-refresh-2026-10-27.md` 的 P1-L 与能力矩阵「工具面」行。

### 13.8 已知残留(未在本期解决)

- **不做 gate 拦截**:清单不阻塞交付(交付前不强制所有项 completed)。加 interrupt 会破坏「interrupt 之前无副作用」的既有约束,留给 M4b 用 verify 提示约束。
- **不做 in_progress 停留提醒/超时提醒**(M4b):需要按轮次/时间判断,当前只在提示词里给纪律。
- **不合并 plan_markdown**:计划(markdown 审批)与任务清单(结构化三态)是两份东西,不互相解析初始化。
- **chat 路径不暴露** `todo_write`:对话没有 run,清单无处落库。
- **清单不跨 run 复用**:每个 run 从空清单开始(retry 沿用同一 run 的快照,符合「同一 run 可复现」)。
- **P1-D item 化未做**:`run.todo.updated.v1` 目前是独立事件类型,P1-D 落地后应提升为 `item`(kind=todo,content→title、status→state)。
