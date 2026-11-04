# 代码执行面设计:run_code(P5)

> 状态:**已落地(2026-11-04)**。范围:在 `services/tool-service` 目录新增一等工具 `run_code`——把模型给的 **Python 3 源码经 stdin** 交给沙箱里的 `python3 -` 执行,返回真实退出码与 stdout/stderr;审批、审计、限额、幂等、租户隔离全部复用既有链路,orchestrator 不加新代码。
> 关联:`docs/design/tool-service.md`(决策 D14 与分期 P5)、`docs/design/tool-execution.md`(exec 单册,本册的结构范本)、`docs/design/connector.md`(连接器工具的风险分档)、`docs/gap-2026-10/08-refresh-2026-10-27.md`(工具面能力矩阵)。

## 1. 摘要

`exec`(P1-L)把「一次性 shell 命令」打通后,还剩一个高频但别扭的场景:**多行 Python**。用 `exec` 只有两条路——`python3 -c "..."`(换行、引号、`$`、反斜杠都要两层转义,20 行以上基本不可读)或 heredoc(要挑分隔符、要防正文里出现同一个 `EOF`)。而模型天然产出的是「一段源码」,不是「一条 shell 行」。

`run_code` 把这段源码直接交给 `python3 -`:代码走 stdin,不进 argv、不经 shell、不参与任何字符串拼接。执行语义、输出裁剪、超时、审批、审计与 `exec` 完全同构,只是入口形态不同。

五条关键决策:

1. **双工具分工,不是替代**:`exec` 继续负责一次性 shell(管道、构建、测试命令、`git`、包管理器);`run_code` 只负责 Python 3 脚本。两者都在沙箱内、都是 `destructive`、都复用同一条审批链。
2. **代码不经 shell、不进 argv**:`sandbox.exec(argv=["python3", "-"], stdin=code)`。省掉整层转义,也避免把模型内容拼进命令行(进程标题、`ps`、下游请求日志里都不会出现代码正文)。
3. **复用 `exec` 的进程直跑实现**:ensure/exec/touch、超时翻译、16K/8K 截断与 25K 回缩抽在 `process_path.ProcessExecutionMixin._run_process()`,exec 与 run_code 只差 argv/stdin 与错误文案,结果信封与错误码一字不差。
4. **默认关闭(`TOOL__RUN_CODE_ENABLED=false`)**:与 `exec` 同档 `destructive`。默认关保证既有部署行为零变化;开启后 orchestrator 的 plan 闸(`_has_dangerous_tools`)会对**所有** run 触发人工确认,confirm 闸对 `danger` 回 CONFIRM——orchestrator 侧零改动。
5. **非零退出码不是执行失败**:与 `exec` 一致,`ok=true`、`exit_code` 原样返回,失败信息在 stderr(traceback 在 stderr),模型据此自我修正。

主流在这一题上分成两类形态:Claude Code / Codex CLI / Gemini CLI / Cline / Continue / goose 只给**一个 shell 工具**(代码靠 shell 拼);OpenHands、Bedrock AgentCore Code Interpreter、E2B、DSH 给 **shell + 代码执行双工具**。本服务选后者,对照与理由见 §4。

## 2. 现状(证据)

| 能力        | 落地前                                                                  | 位置                                                                 |
| ----------- | ----------------------------------------------------------------------- | -------------------------------------------------------------------- |
| 命令执行    | 已有 `exec`(P1-L):一次性 `/bin/sh -lc`,cwd 夹在 `/workspace` 内、默认关 | `services/tool-service/tool_service/services/execution/exec_path.py` |
| 多行 Python | 只能 `python3 -c "..."` 或 heredoc,转义在 20 行以上不可维护             | 由模型在对话/计划里自行拼接,无工具级入口                             |
| 执行协议    | `exec` 已确立「不进 helper、直调 `SandboxProvider.exec`」               | `services/tool-service/tool_service/services/sandbox.py`             |
| 沙箱隔离    | microVM(microsandbox)默认,`sandbox_network=deny`,沙箱 TTL 1800s         | `services/workspace-service/workspace/services/sandbox_provider.py`  |
| 目录        | 10 个工具(含 `exec`),没有代码执行入口                                   | `services/tool-service/tool_service/services/catalog.py`             |
| 结果上限    | 整包 25,000 字符;`exec` 已自带 16K/8K 截断与回缩                        | `services/tool-service/tool_service/constants.py`                    |

## 3. 目标与非目标

### 3.1 目标

1. **多行 Python 一次成型**:模型直接给源码,不必考虑 shell 引号、heredoc 分隔符与续行;traceback 行号与源码行号一一对应。
2. **与 `exec` 同一套可观测性**:退出码、stdout/stderr 截断、notes 回读提示、`tool_calls` 审计行、`run.tool.called.v1` 事件,不新增旁路。
3. **边界可控**:默认关 + 逐次确认 + 沙箱隔离 + `cwd` 夹取 + 120s 超时上限。
4. **关闭时行为与改造前完全一致**:目录、执行、审批链路都不变。

### 3.2 非目标

- **不做持久内核/REPL**:每次调用是一个新的 `python3 -` 进程,变量、导入、monkey-patch 都不跨调用(OpenHands 的 IPython 会话、E2B 的常驻 kernel 是另一种形态,P5-b 再议)。
- **不做富输出**:只回文本 stdout/stderr;图表、DataFrame HTML、二进制不转图片/base64。需要取回就落盘,再用 `read`(文本)或交付物通道。
- **不预装第三方库**:`network=deny` 不变,`pip install` 会失败;镜像即环境,这是与 `exec` 共用的有意边界。
- **不做代码静态审查/import allowlist**:依赖 P1-A policy 引擎,本册保留全量确认。
- **不替换 `exec`**:shell 语义(管道、重定向、构建脚本、`&&`)仍只有 `exec` 能做。
- **不落完整代码**:审计只存 `arguments` 前 2,000 字符 + `arguments_sha256`。

## 4. 与主流对照

| 方案                                           | 执行工具形态                                           | 多行代码怎么送                     | 我们的取舍                                        |
| ---------------------------------------------- | ------------------------------------------------------ | ---------------------------------- | ------------------------------------------------- |
| Claude Code                                    | `Bash`(持久 shell 会话)                                | 只能 shell:heredoc 或 `python3 -c` | 单一 shell 工具的典型;我们多给一个代码入口        |
| OpenAI Codex CLI                               | `exec_command`(PTY)+ `write_stdin` + `apply_patch`     | 只能 shell;交互靠 `write_stdin`    | 不做 PTY 会话,只取「一次性执行」                  |
| Gemini CLI                                     | `run_shell_command`                                    | 只能 shell                         | 同上                                              |
| Cline / Continue                               | `run_commands` / `runTerminalCommand`                  | 只能 shell                         | 同上                                              |
| Cursor                                         | `run_terminal_cmd`(持久终端)                           | 只能 shell                         | 同上                                              |
| OpenHands                                      | `BashTool` + `IPythonRunCellTool`(Jupyter 内核)        | 双工具:代码作为 cell 提交给内核    | **同形态**(代码入口独立);不做持久内核             |
| goose / Aider                                  | goose 走 developer extension 的 shell;Aider 无任意执行 | 只能 shell / 无                    | 不作为执行面基线                                  |
| LangGraph / Bedrock AgentCore Code Interpreter | code interpreter 会话(`executeCode`)                   | 代码直接提交给解释器会话           | 对齐「代码不经 shell」;隔离换成本服务既有 microVM |
| E2B                                            | `runCode`(代码解释器 SDK,可长期 kernel)                | 代码直接提交                       | 对齐入口形态;不做常驻 kernel                      |
| DSH(本环境)                                    | `bash` + `run_code`(双工具)                            | 代码走 stdin 直传解释器            | **直接对标**,本册即其云端等价物                   |
| **beauty-moon(本册)**                          | `exec`(`sh -lc`)+ `run_code`(`python3 -`)              | 代码走 stdin                       | 双工具;复用 exec 的沙箱、审批、审计、信封         |

结论:主流分两类——**单一 shell 工具**(Claude Code / Codex CLI / Gemini CLI / Cline / Continue / goose)零新增工具面,但多行代码的可读性与正确性全靠模型自己保证;**shell + 代码执行双工具**(OpenHands / Bedrock AgentCore Code Interpreter / E2B / DSH)多一个专用入口,换来「代码不经 shell」。我们选第 2 类:成本是目录多一个 `destructive` 工具,已被「默认关 + 同一审批链 + 同一信封」抵消;收益是多行 Python 不再依赖转义,代码也不进入命令行与进程标题。持久内核与富输出属于增速项,明确延后。

## 5. `run_code` 与 `exec` 的职责边界

| 维度        | `exec`                                         | `run_code`                                                |
| ----------- | ---------------------------------------------- | --------------------------------------------------------- |
| 输入        | `command`:一条 shell 命令,可含管道/重定向/`&&` | `code`:完整 Python 3 源码(多行)                           |
| 落地 argv   | `[/bin/sh, -lc, command]`,无 stdin             | `["python3", "-"]`,代码走 stdin                           |
| 适合        | 构建、测试、`git`、包管理、管道、环境探查      | 数据处理、计算、批量生成文件、带循环/函数的多行脚本       |
| 不适合      | 多行 Python(转义地狱)                          | shell 语义(管道/重定向/构建脚本)、需要交互式 shell 的场景 |
| 解释器      | 沙箱内 `/bin/sh`                               | 沙箱内 `python3`(与文件 helper 同一个 `HELPER_PYTHON`)    |
| 工作目录    | 默认 `/workspace`,由 `resolve_exec_cwd()` 夹取 | 同左(同一个函数、同一套规则)                              |
| 风险档/默认 | `destructive` / `TOOL__EXEC_ENABLED=false`     | `destructive` / `TOOL__RUN_CODE_ENABLED=false`            |
| 信封/裁剪   | 16,000 / 8,000 / 25,000-600                    | 完全同构(共用 `_run_process`)                             |
| 审批/审计   | plan 闸 + confirm 闸 + 403 兜底 + `tool_calls` | 完全同构(同一个 `decision` 口径)                          |

怎么选(写进工具描述的三句话):

1. 能一条 shell 命令说清的,用 `exec`;
2. 要写多行脚本(循环、函数、`json`/`csv` 处理)的,用 `run_code`;
3. 两者都能做时选读起来更短的那个——**不要**用 `exec` 拼 `python3 -c` 的长字符串。

## 6. `run_code` 契约

| 字段            | 值                                                |
| --------------- | ------------------------------------------------- |
| name            | `run_code`(裸 snake_case,与 `exec` 同风格)        |
| risk            | `destructive`(legacy `danger`)                    |
| kind / executor | `function` / `sandbox`                            |
| timeout_seconds | 60(descriptor 默认;单次请求可覆盖到 1..120)       |
| 目录位置        | `TOOLS` 中紧接 `exec`、在 `search_knowledge` 之前 |
| catalog_version | `2026-11-04.p6`(TOOLS 共 11 个工具)               |

参数(`additionalProperties: false`,`required: ["code"]`):

| 参数              | 类型    | 约束               | 说明                                                                                              |
| ----------------- | ------- | ------------------ | ------------------------------------------------------------------------------------------------- |
| `code`            | string  | 必填,1..20000 字符 | 完整 Python 3 源码;等价于从 stdin 传给 `python3 -`,顶层语句直接执行,不会自动调用 `main()`         |
| `cwd`             | string  | 可选,1..512 字符   | 工作目录,相对路径按 `/workspace` 解析,默认 `/workspace`;绝对路径也接受,但必须落在 `/workspace` 内 |
| `timeout_seconds` | integer | 可选,1..120        | 本次代码执行超时(秒);省略时用 descriptor 默认 60,显式给出时按 1..120 夹取后生效                   |

### 6.1 成功信封

代码跑完即 `ok=true`(与 `exec` 同构),**非零退出码不是执行失败**:

```json
{
  "ok": true,
  "truncated": false,
  "duration_ms": 812,
  "tool_call_id": "call_01",
  "result": {
    "exit_code": 0,
    "stdout": "rows=1200\nmean=31.7\n",
    "stderr": "",
    "cwd": "/workspace/reports",
    "duration_ms": 812
  }
}
```

| 字段                | 说明                                                                      |
| ------------------- | ------------------------------------------------------------------------- |
| `exit_code`         | 进程退出码;`1` 常见于未捕获异常(traceback 在 stderr),`124` 表示被超时终止 |
| `stdout` / `stderr` | 文本输出(已截断)                                                          |
| `truncated`         | 任一输出被截断(顶层同名字段与 `exec` 一致)                                |
| `duration_ms`       | 沙箱侧执行耗时(缺失时用本地计时)                                          |
| `cwd`               | 实际生效的绝对工作目录                                                    |
| `notes`             | 截断/回读提示(仅截断时出现)                                               |

### 6.2 失败信封

| code                         | 场景                                                                            | retryable |
| ---------------------------- | ------------------------------------------------------------------------------- | --------- |
| `tool.invalid_args`          | `cwd` 逃出 `/workspace`/含非法字符;schema 之外的参数校验失败                    | false     |
| `tool.timeout`               | 沙箱获取超时、本地等待超时(「代码执行超时(N 秒)」)、下游 `sandbox.exec_timeout` | true      |
| `tool.upstream_error`        | 沙箱不可用/连接失败/下游 5xx(含其它 `UpstreamError`)                            | true      |
| `tool.not_found`             | `run_code` 未开启(API 404,能力即权限边界)                                       | false     |
| `tool.confirmation_required` | 未带 `approved=true`(API 403,零副作用)                                          | false     |
| `tool.budget_exceeded`       | 单 run 工具调用次数超限                                                         | false     |

注意:`code` 的类型/长度由 schema 先拦(同样回 `tool.invalid_args`),这些失败**不触达沙箱**——测试里可以直接断言 `sandbox.ensure` 未被调用。

### 6.3 截断与体积

- stdout 超过 **16,000** 字符 → 保留**头部**并置 `truncated=true`,附回读提示。
- stderr 超过 **8,000** 字符 → 保留**尾部**(错误栈末尾才有最终异常),附回读提示。
- 截断后再按整包 JSON 体积回缩到 `MAX_TOOL_CHARS - EXEC_NOTES_RESERVE`(25,000 - 600)以内;先缩 stdout 到 2,000,再缩 stderr 到 1,000,到底后对半砍且不超过各自下限。**`run_code` 不会回 `tool.too_large`**。
- 想要完整输出:在代码里写文件(`open("reports/out.txt", "w")`)再用 `read` 分段读,或走交付物通道。

## 7. 执行路径

```text
orchestrator 工具调用
  → POST /v1/tools/run_code:execute
      → 目录查找 + is_available(TOOL__RUN_CODE_ENABLED)
      → 幂等重放检查(tool_call_id)
      → destructive 且未 approved → 403 tool.confirmation_required
      → 单 run 调用次数配额
      → ToolRunner.execute → _run_code
          → 参数校验 → cwd 夹取(resolve_exec_cwd)
          → sandbox.ensure → sandbox.exec(["python3", "-"], stdin=code, cwd)
          → 截断/回缩 → 成功信封 → touch(TTL 续期)
      → tool_calls 审计落库
```

`_run_code` 的关键点:

1. **argv 固定 `["python3", "-"]`**:解释器名复用 `helpers.HELPER_PYTHON`(值 `"python3"`),不依赖镜像里 `python` 的别名;`-` 是 POSIX 标准含义「从 stdin 读脚本」,不需要临时文件。
2. **代码只走 stdin**:`stdin=str(arguments["code"])`,与 `exec` 的 `stdin=None` 是两种入口形态;除 stdin 外没有任何地方复制代码正文。
3. **cwd 与 exec 共用 `resolve_exec_cwd()`**:`None`/空串 → `/workspace`;相对路径按 `/workspace` 解析;`posixpath.normpath` 归一后必须等于 `/workspace` 或是其子路径,否则回 `tool.invalid_args`(「cwd 必须落在工作区 /workspace 内」)。这**不是**安全边界(代码内仍可 `os.chdir`),真正的边界是沙箱。
4. **超时**:`_run_code_timeout` 走 `_override_timeout(..., MAX_RUN_CODE_TIMEOUT_SECONDS=120)` 夹在 1..120;沙箱调用另包一层 `asyncio.wait_for(timeout + SANDBOX_CALL_GRACE_SECONDS)` 兜底,本地等待超时的文案是「代码执行超时(N 秒)」。
5. **共用 `_run_process`**:ensure/exec/touch、`TimeoutError` → `tool.timeout`、`UpstreamError` → 超时/上游故障映射、`_clip_exec_stream`/`_fit_exec_result`/notes 文案都在 `process_path.ProcessExecutionMixin`,exec 与 run_code 只有 `argv`/`stdin` 与 `label`(「代码」/「命令」)不同。
6. **TTL 续期**:`finally` 里 `touch`,失败不影响结果(与文件工具、`exec` 一致)。

### 7.1 `python3 -` 的语义(写进工具描述,避免模型误用)

- **`__name__ == "__main__"`**:顶层语句直接执行;**不会**自动调用 `main()`,要调用需自己写 `if __name__ == "__main__": main()`。
- **没有 `__file__`**:用 `Path(__file__)` 会抛 `NameError`;需要脚本路径时用 `sys.argv[0]`(值为 `-`),或自己写死相对路径。
- **traceback 文件名是 `<stdin>`**:行号与源码行号一一对应,报错位置可直接对上模型给的代码。
- **`input()` 立即 EOF**:没有交互式 stdin,读到 `EOFError`;需要数据就写在代码里或读工作区文件。
- **`sys.argv == ["-"]`**:不要指望传参,参数写在代码里。

### 7.2 为什么代码不进 argv

- **转义**:经 shell 要两层转义;即使直传 `SandboxProvider.exec`(不经 shell),单参数长度也受限,且仍要把代码复制进每一层日志。
- **可见性**:`sandbox.exec(argv=[...])` 的 argv 会出现在下游请求体与错误日志里;代码放 stdin 只多一个请求字段,不额外扩散。
- **审计不损失**:审计行本来就存 `arguments`(含代码),argv 再存一份没有意义。
- **复用**:`python3 -` 无需临时文件、无需 `echo | python`,与 `exec` 的直调协议同一条路径。

### 7.3 与 helper 协议的关系

文件工具走沙箱内 `fs_helper`(stdin JSON 请求 + JSON 信封 + 路径 jail)。`run_code` 要真实退出码、真实 traceback 与任意文本输出,套 JSON 信封会既丢信息又多一层解析,因此与 `exec` 一样直调 `SandboxProvider.exec`。

## 8. 审批与安全

### 8.1 风险档为什么是 destructive

Python 进程能做 shell 能做的所有事,而且更省事:`os.system`/`subprocess` 起子进程、`shutil.rmtree` 删文件、读环境变量、在不走 helper 路径 jail 的情况下读写沙箱内任意文件、在沙箱内发起网络请求(策略上 deny)。风险等价于**任意代码执行**,而不是普通写入,因此按 `destructive`(legacy `danger`)对待。

### 8.2 审批链(与 `exec` 完全一致,orchestrator 零改动)

1. **orchestrator plan 闸**:启用目录里存在危险工具时,`approval_gate_node` 一定 `interrupt`(kind=plan),人工确认后才执行计划。
2. **confirm 闸**:`RiskDefaultApproval` 对 `danger` 返回 CONFIRM → `interrupt`(kind=confirmation),批量 `writes_approved` 后以 `approved=true` 执行。
3. **tool-service 兜底**:`execute` 与 `ToolRunner` 都做「WRITE/DESTRUCTIVE 无 approved → 403」,防越权直连。
4. **目录开关**:`TOOL__RUN_CODE_ENABLED=false` 时 list/get/execute 一致地视为不存在(404 `tool.not_found`),不会有「目录没有但能执行」的缝。
5. **对话路径不可见**:chat 侧把工具裁剪为 `kind=function && risk!=danger`;`run_code` 是 `destructive`(legacy `danger`),与 `exec` 一样**不进对话**,只在任务图里走审批(见 [tool-execution.md](./tool-execution.md) §7.6)。

### 8.3 打开 run_code 的部署前提

- 沙箱必须用 `sandbox_provider=microsandbox`(microVM);`local` provider 无隔离,只允许本地单租户开发。
- `sandbox_network=deny` 保持默认;**不要**为了 `pip install` 打开网络。
- 单 run 配额(`TOOL__MAX_TOOL_CALLS_PER_RUN`)与沙箱 TTL 是唯一的时间/次数兜底,生产建议调低。
- 需要人工确认 UI(orchestrator 的 confirmation 闸);无人值守的自动化 run 不应开启。
- 只开 `run_code` 不开 `exec` 是允许的(两个开关独立),但建议同开同关:否则模型会绕过 `exec` 用 Python 调 `os.system`,审批口径反而更难解释。

### 8.4 已知风险与缓解

| 风险                          | 缓解                                                                     |
| ----------------------------- | ------------------------------------------------------------------------ |
| 资源耗尽(CPU/内存/长跑进程)   | 沙箱 cpus/memory 上限 + 120s 超时 + microVM 硬隔离 + 单 run 调用次数上限 |
| 输出洪水                      | 16K/8K 截断 + 25K 硬上限 + notes 提示回读                                |
| 沙箱内副作用(写文件/删交付物) | 沙箱隔离 + 逐次确认 + TTL;run 级写配额不覆盖(见 §8.5)                    |
| 沙箱内 exfil(网络)            | `network=deny`;不开放 egress,`fetch` 与 `run_code` 互不替代              |
| 依赖安装(供应链)              | 默认失败;需要预装依赖应在镜像/工作区准备阶段完成                         |
| microVM 逃逸                  | 依赖 microsandbox 上游;紧跟安全公告,不以 `run_code` 为信任边界           |
| 代码注入                      | 代码本身就是模型给的源码,不经拼接;/bin/sh 不参与;审批与沙箱是唯一防线    |
| 审计残留代码片段(可能含密钥)  | `arguments` 只留前 2,000 字符 + sha256;不要把密钥写进代码                |

### 8.5 已知残留:run 级写配额不覆盖沙箱内副作用

`TOOL__MAX_WRITES_PER_RUN` / `TOOL__MAX_WRITE_BYTES_PER_RUN` 只统计 `write`/`edit` 两个工具;沙箱里的 Python 代码写了多少字节,工具面看不到。当前边界靠三层:沙箱(microVM + TTL)、工具级审批(每次调用人工确认)、run 级总调用次数。要做「run 级沙箱写预算」需要沙箱侧计量(microsandbox 磁盘用量或工作区快照 diff),留到后续迭代。

### 8.6 与 P1-A 的关系

P1-A policy 引擎落地后,可以对 `run_code` 做「只读意图」或「import allowlist」之类的静态规则,或按 run 类型自动放行;本册先保留全量确认。

## 9. 审计与事件

- **审计行**:`tool_calls` 记录 `tool_name=run_code`、`risk=destructive`、`decision=approved`、`arguments`(JSON,含代码,截 2,000 字符,代码可能被截断)、`arguments_sha256`(全文摘要,可判重)、`duration_ms`、`result_chars`、`sandbox_id`、`ok/error_code`、`truncated`。`exit_code` 在结果里,不新增数据库列(不改迁移)。
- **事件**:`run.tool.called.v1` 由 orchestrator 从审计行增量生成,`_tool_event_payload` 白名单带 arguments/output 截断;与 `exec` 同一路径。
- **可观测**:前端 `/runs` 详情抽屉的工具调用列表直接显示 arguments(代码片段),不需要新 UI。
- **代码全文不落库**:需要复现时从会话消息历史取模型给的原始 `tool_call`。

## 10. 配置

| 变量                             | 默认    | 说明                                              |
| -------------------------------- | ------- | ------------------------------------------------- |
| `TOOL__RUN_CODE_ENABLED`         | `false` | 开启 `run_code`;关闭时目录不暴露、GET/execute 404 |
| descriptor `timeout_seconds`     | `60`    | 单次代码默认超时;请求可在 1..120 覆盖             |
| `TOOL__TOOL_TIMEOUT_SECONDS`     | `30`    | 全局默认(`run_code` descriptor 覆盖为 60)         |
| `TOOL__MAX_TOOL_CALLS_PER_RUN`   | `60`    | 单 run 工具调用次数上限(含 `run_code`)            |
| `TOOL__SANDBOX_TOOL_TTL_SECONDS` | `1800`  | 沙箱滑动 TTL                                      |

orchestrator 侧无新配置:危险工具经目录自动进入审批链。

## 11. 测试计划

| 用例组     | 断言                                                                                   |
| ---------- | -------------------------------------------------------------------------------------- |
| 目录与开关 | 默认:list 不含 `run_code`、GET 404、execute 404;开启:出现且 risk/executor/timeout 正确 |
| 执行成功   | argv 为 `["python3", "-"]`、`stdin` 为代码原文、cwd 默认 `/workspace`、信封字段齐全    |
| 非零退出   | `ok=true`、`exit_code` 原样、stderr 带 traceback                                       |
| 截断       | stdout 20K → 16K(保头)、stderr 保尾、`truncated=true`、notes 有回读提示                |
| 体积回缩   | 极端输出下整包 JSON ≤ 25,000                                                           |
| cwd 夹取   | 相对路径归一、`..` 逃逸与 `/etc` 拒绝、`/workspace2` 不误伤                            |
| 超时       | 请求 `timeout_seconds` 生效;下游 `sandbox.exec_timeout` → `tool.timeout`               |
| 上游故障   | `tool.upstream_error`、retryable=true                                                  |
| 审批       | 无 `approved` → 403 且不触达沙箱                                                       |
| 审计       | `tool_calls` 行 decision=approved、arguments 含代码、`tool_name=run_code`              |
| 零网络     | 全部打桩 `FakeSandboxProvider`,不发真实请求                                            |

## 12. 分期与验收

| 期   | 内容                                               | 状态                         |
| ---- | -------------------------------------------------- | ---------------------------- |
| P5   | `run_code` 落地:目录/开关/执行/审批/审计/截断/测试 | **已落地(2026-11-04)**       |
| P5-b | 持久内核、富输出(图片/DataFrame)、代码静态审查     | 待启动(依赖 P1-A 与沙箱能力) |

P5 验收:

1. 开启 `run_code` 后,一个 run 能把「多行 Python 脚本」一次执行完,退出码与输出在 `/runs` 可见。
2. 关闭时,目录、执行、审批链路与改造前完全一致。
3. `tool-service` 全量测试通过,覆盖率不降(门禁 82)。

## 13. 实施记录

### 13.1 文件清单

| 文件                                                                     | 改动                                                                                                                   |
| ------------------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------- |
| `services/tool-service/tool_service/services/catalog.py`                 | 新增 `RUN_CODE` 描述符(共 11 个工具);`CATALOG_VERSION` 升至 `2026-11-04.p6`                                            |
| `services/tool-service/tool_service/services/execution/run_code_path.py` | 新增:`_run_code`(`argv=["python3", "-"]`、代码走 stdin、cwd 夹取、超时口径)                                            |
| `services/tool-service/tool_service/services/execution/process_path.py`  | 新增:ensure/exec/touch、超时翻译、截断/回缩,exec 与 `run_code` 共用                                                    |
| `services/tool-service/tool_service/services/execution/exec_path.py`     | 收敛为只做参数校验与 shell argv 组装                                                                                   |
| `services/tool-service/tool_service/services/execution/runner.py`        | 分派 `run_code` → `_run_code`                                                                                          |
| `services/tool-service/tool_service/services/execution/arguments.py`     | `is_available()` 增加 `run_code` 分支(读 `settings.run_code_enabled`)                                                  |
| `services/tool-service/tool_service/core/config.py`                      | `run_code_enabled: bool = False`                                                                                       |
| `services/tool-service/tool_service/constants.py`                        | `MAX_RUN_CODE_CHARS`/`DEFAULT_RUN_CODE_TIMEOUT_SECONDS`/`MAX_RUN_CODE_TIMEOUT_SECONDS`                                 |
| `services/tool-service/tests/test_tool_run_code.py`                      | 新增:开关/目录、执行成功与非零退出、cwd 夹取、超时与上游故障、审批 403 与审计(31 例)                                   |
| `services/tool-service/tests/test_catalog.py`/`tests/conftest.py`        | run_code 进入目录断言与测试开关夹具                                                                                    |
| 文档                                                                     | 本文 + `tool-service.md`(D14/P5/工具清单/`CATALOG_VERSION` 注记)+ `tool-execution.md` 交叉引用 + README/`.env.example` |

### 13.2 与本文的差异

- 实现把 `exec` 的进程直跑逻辑抽成 `process_path.py` 共用(原计划各自实现),结果信封与错误码保持不变;`run_code` 与 `exec` 只差 `argv`/`stdin` 与错误文案。
- 本地等待超时的文案随 `label` 变化(`exec` 为「命令执行超时(N 秒)」,`run_code` 为「代码执行超时(N 秒)」)。
- 唯一契约偏差:`RUN_CODE.input_schema.properties.timeout_seconds` 初版漏了 `"default": 60`,已按契约补齐;同时把 `code.maxLength` 与 `timeout_seconds.maximum` 改为引用 `MAX_RUN_CODE_CHARS` / `MAX_RUN_CODE_TIMEOUT_SECONDS`,避免常量与 schema 双源。除此之外实现与本文一致。

### 13.3 验证

- `npx nx test tool-service`:**375** passed,覆盖率 **90.11**%(门禁 82);全部打桩 `FakeSandboxProvider`,零网络。
- 定向:`tests/test_tool_run_code.py` **31** 例(另 `tests/test_catalog.py` 10 例)。
- `npx nx lint tool-service` / `npx nx format tool-service`:均通过(ruff check / ruff format --check 无告警)。
- **orchestrator 未改代码**:`RemoteToolkit.risk()` 把 `risk=destructive` 映射为 legacy `danger` → plan 闸自动 `interrupt`;confirm 闸对 `danger` 回 CONFIRM;chat 只装配 READ/WRITE,`run_code` 在对话里不可见。
- 文档:`bash scripts/check-docs.sh` 通过(prettier + 相对链接/锚点);`services/tool-service/README.md`、`.env.example` 已同步。

### 13.4 已知残留(未在本期解决)

- **run 级写配额不覆盖沙箱内副作用**:`MAX_WRITES_PER_RUN`/`MAX_WRITE_BYTES_PER_RUN` 只看 `write`/`edit`(§8.5)。
- **chat 路径用不到 `run_code`**:对话只装配 `risk!=danger` 的工具;要在对话里跑代码需 P1-A 放开只读档。
- **无持久内核**:每次调用一个新进程,变量/导入不跨调用。
- **无富输出**:只回文本,图表/DataFrame 需落盘再取。
- **审计代码截断**:`arguments` 只留 2,000 字符,完整代码只在会话消息历史里。
