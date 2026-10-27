# workspace-service

工作区服务(架构 §3/§7):**工作区 + 交付物元数据 + 对象存储 + 沙箱生命周期**。

§7 数据模型要点把 `artifacts` 归属到本服务(实体文件在对象存储,COS/R2 阶段 B)。

- `POST /v1/workspaces`、`GET /v1/workspaces`、`GET /v1/workspaces/{id}`:
  工作区(团队资料空间;返回体带 `is_default` 标记);
- `GET /v1/workspaces/default`:当前租户的默认工作区(**幂等建档**),
  agent-orchestrator 交付时往这里推;
- `PATCH /v1/workspaces/{id}`:改名/改描述(部分更新):未传字段不改,
  `description` 显式传 `null` 清空;同租户重名 409 `workspace.exists`,
  未知/跨租户 404 `workspace.not_found`;默认工作区**可以改名**;
- `DELETE /v1/workspaces/{id}`(204):删除工作区并**级联清理其交付物**
  (对象存储里的对象 + `workspace_artifacts` 行);默认工作区不可删,
  返回 409 `workspace.default_protected`;未知/跨租户 404;
- `POST /v1/artifacts`(标题/类型 + 正文或二进制,可带 `run_id`):body 里
  `content`(文本)与 `content_base64`(二进制)**恰好给一个**(都给/都不给 422),
  可带 `filename`/`content_type`;
- `GET /v1/workspaces/{id}/artifacts`(元数据列表)、`GET /v1/artifacts/{id}`
  (元数据 + 文本正文)、`GET /v1/artifacts/{id}/download`(原始字节 + 附件头
  `Content-Disposition: attachment; filename*=UTF-8''…` + `Cache-Control: private, no-store`)、
  `DELETE /v1/artifacts/{id}`;
- `/healthz` `/readyz`:存活/就绪探针。

### 默认工作区与删除语义

- `is_default`:每个租户有且只有一个系统维护的默认工作区。解析顺序是
  `is_default` 标记 → 历史名称兜底(老数据命中后补标记)→ 新建;因此
  **默认工作区被改名后仍然认得出**,不会在下一次交付时重复建档
  (迁移 `b9d4e6f1a2c3` 把既有的「自动创建:租户默认工作区」行置为默认);
- 默认工作区**只可改名、不可删除**:编排/会话默认往它落交付物,删掉会让
  大量弱引用悬空;要换落点请另建工作区;
- 删除非默认工作区是**本服务内的级联**:先删对象存储中的对象(单个失败只记
  warning,不阻塞删除,行仍会删掉),再删 `workspace_artifacts` 行,最后删
  工作区行。跨服务引用(agent-orchestrator 的会话/run、沙箱实例)只有 id、
  没有外键,**本服务不越权清理**:编排侧删除后会话落「未分组」,交付物的
  run 溯源随之失效——这是弱引用有意接受的代价。

| 场景                           | 错误码                        | HTTP |
| ------------------------------ | ----------------------------- | ---- |
| 同租户工作区重名(创建/改名)    | `workspace.exists`            | 409  |
| 删除默认工作区                 | `workspace.default_protected` | 409  |
| 工作区不存在或跨租户(读/改/删) | `workspace.not_found`         | 404  |

交付物内容通过 `common.artifacts.ArtifactStore` 落盘:

- 文本类(`text/*`、JSON/YAML/XML 等)进 `content` 列;二进制进 `content_bytes`
  列(下载端点按 MIME 取原始字节,不再假设"交付物一定是 Markdown");
- `ARTIFACT_STORE=database`:上述两列即正文;
- `ARTIFACT_STORE=filesystem`(默认):字节直接写 `ARTIFACT_ROOT`
  (键 `<租户>/<工作区>/<交付物><安全后缀>`,后缀由 MIME 推导、bin 兜底),
  行里只留 `object_key`/`size_bytes`/`filename`/`content_type`;
- 换 S3/COS 只需实现该协议,读端契约不变。

## 对象存储 API(P3:skill 包)

通用二进制对象接口,供 agent-orchestrator 存用户上传的 skill 包(zip):内容直接落
`ArtifactStore`,**不登记 `workspace_artifacts` 行**(本 API 没有 DB 表),
因此与交付物元数据互不影响。

- `POST /v1/objects`(201):body `{filename?, content_type?, content_base64}`
  (默认 `application/octet-stream`),返回 `{key, size_bytes, sha256}`。
  `filename` **只用于安全后缀**(`.zip`/`.skill` 等,默认空),不参与路径;
  key 形如 `<租户>/objects/<uuid4hex><后缀>`;
- `GET /v1/objects?key=`:返回 `{key, content_base64, size_bytes, sha256}`;
- `DELETE /v1/objects?key=`(204):幂等,只删对象,不删任何 DB 行。

错误码:

| 场景                                          | 错误码                   | HTTP |
| --------------------------------------------- | ------------------------ | ---- |
| Base64 非法                                   | `object.content_invalid` | 400  |
| 超过 `OBJECT_MAX_BYTES`                       | `object.too_large`       | 400  |
| key 不存在或不属于当前租户                    | `object.not_found`       | 404  |
| 对象存储后端未配置(`ARTIFACT_STORE=database`) | `object.store_missing`   | 503  |

跨租户读取/删除一律 404(与不存在不可区分);`size_bytes`/`sha256` 均对解码后的
原始字节计算。配置:`WORKSPACE__OBJECT_MAX_BYTES`(默认 `20971520`,即 20MB)。

## 沙箱(§7 阶段 B / P1)

执行底座默认是 [microsandbox](https://github.com/superradcompany/microsandbox)
(microVM 隔离),为后续**用户 skill / stdio MCP** 提供执行环境。**P1 已落地**:provider
抽象、沙箱实例生命周期、HTTP API 与过期回收;stdio MCP / skill 打包在 P2/P3。

`WORKSPACE__SANDBOX_PROVIDER` 有三个取值:`microsandbox`(**默认**,生产/多租户唯一
推荐)、`local`(宿主目录 + 宿主子进程,**无隔离**,仅本地开发/单租户可信环境,见下文
「local provider」)与 `unavailable`(调用即 502,供没有 runtime 的 dev 显式失败)。

接口:

- `POST /v1/sandboxes`(201):创建沙箱。body 支持 `kind`(skill|mcp|generic)、
  `image`、`ttl_seconds`、`cpus`、`memory_mb`、`network`(deny|public)、
  `workspace_id`、`run_id`;不传资源时用服务端默认值。`deny` 完全断网;
  `public` 只放公网(走 microsandbox `NetworkProfile.PUBLIC`),**不包含宿主/私网**;
- `GET /v1/sandboxes?scope_key=`:沙箱列表(仅本租户,新建在前);`scope_key`
  精确过滤到某个会话/run(例如工具调用按 `conv:<id>` 找回执行现场);
- `GET /v1/sandboxes/{id}`:实例详情;
- `POST /v1/sandboxes/{id}/exec`:执行命令(`argv` 不经过 shell),
  返回 `exit_code/stdout/stderr/duration_ms/truncated`,成功后滑动续期
  (单次 TTL 上限 `sandbox_max_ttl_seconds`);
- `PUT /v1/sandboxes/{id}/files`(204)、`GET /v1/sandboxes/{id}/files?path=`:
  文件读写,内容 Base64;路径必须在 `/workspace` 下且不能含 `..`;
- `GET /v1/sandboxes/{id}/tree?path=/workspace&depth=1`:文件树。返回
  `{path, entries, truncated}`,`entries` 是**扁平列表**(含各级嵌套项),
  每项 `{path, name, kind, size_bytes, modified}`:`path` 是沙箱内绝对路径、
  `kind` 取 `file|dir|symlink|other`(符号链接只标记、不递归)、目录
  `size_bytes` 恒为 0、`modified` 是 epoch 秒;路径同样必须在 `/workspace` 下,
  `depth` 1–4(默认 1),超过 500 条截断并置 `truncated=true`;
- `POST /v1/sandboxes/{id}/touch`:显式续期;
- `DELETE /v1/sandboxes/{id}`(204):先销毁运行时,再删记录;同时关闭该沙箱的
  **全部持久 shell 会话**;
- `POST /v1/sandboxes/{id}/sessions`(201):开一个**持久 shell 会话**(见下文
  「持久 shell 会话」),body `{cwd?, cols?=120, rows?=32}`,返回
  `{session_id, sandbox_id, cwd, created_at}`;
- `POST /v1/sandboxes/{id}/sessions/{sid}/input`(204):向 shell 的 stdin 写
  UTF-8 文本(单次上限 8KB,超出 422);会话不存在/跨租户 404,已关闭 409;
- `GET /v1/sandboxes/{id}/sessions/{sid}/stream`:`text/event-stream` 输出流
  (先回放环形缓冲,再跟随实时输出;支持 `Last-Event-ID` 续传);
- `DELETE /v1/sandboxes/{id}/sessions/{sid}`(204):关闭会话(幂等)。

实现要点:

- 业务层只依赖 `SandboxProvider` 协议;microsandbox **惰性导入**,
  测试与没有 runtime 的 dev 启动不加载 SDK;
- provider 在 lifespan 由 `build_sandbox_provider` 按 `WORKSPACE__SANDBOX_PROVIDER`
  装配;未知取值**启动即失败** `sandbox.provider_unknown`,不静默降级;
- 创建时**先落库** `status=creating` 再拉起运行时;失败保留
  `status=failed` + `last_error`(可追踪),对外统一 502;
- 过期实例在 exec/文件操作前校验:先销毁运行时,再返回 409 `sandbox.expired`;
  后台 reaper(`sandbox_reaper_enabled`/`sandbox_reaper_interval_seconds`)
  周期回收 `expires_at` 已过的实例,单次失败只记录 `last_error`,不阻塞其他实例;
- provider 异常统一收口(不再把 SDK 堆栈泄漏成 500):

| 场景                           | 错误码                                            | HTTP |
| ------------------------------ | ------------------------------------------------- | ---- |
| 运行时未安装/未启用            | `sandbox.provider_unavailable`                    | 502  |
| 运行时操作失败/超时            | `sandbox.provider_error` / `sandbox.exec_timeout` | 502  |
| 沙箱过期                       | `sandbox.expired`                                 | 409  |
| 沙箱不存在/跨租户              | `sandbox.not_found`                               | 404  |
| 路径非法(非 /workspace、含 ..) | `sandbox.path_invalid`                            | 400  |
| 沙箱内文件/目录不存在          | `sandbox.file_not_found`                          | 404  |

配置(env 前缀 `WORKSPACE__`,括号内为默认值):

| 配置项                                                                  | 说明                                                                                                               |
| ----------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
| `SANDBOX_PROVIDER`(microsandbox)                                        | `microsandbox`(**默认**,生产/多租户唯一推荐)真 microVM;`local` 宿主执行、**无隔离**(见下);`unavailable` 调用即 502 |
| `SANDBOX_LOCAL_ROOT`(./.sandboxes)                                      | 仅 `provider=local`:宿主根目录,每个沙箱 `<root>/<name>/{workspace,home,tmp}`,相对**服务进程工作目录**              |
| `SANDBOX_DEFAULT_IMAGE`(python)                                         | microsandbox 镜像别名                                                                                              |
| `SANDBOX_DEFAULT_TTL_SECONDS`(1800) / `SANDBOX_MAX_TTL_SECONDS`(7200)   | 默认/上限 TTL                                                                                                      |
| `SANDBOX_DEFAULT_CPUS`(1.0) / `SANDBOX_DEFAULT_MEMORY_MB`(512)          | 默认配额                                                                                                           |
| `SANDBOX_NETWORK`(deny)                                                 | 默认网络策略(`deny`/`public`)                                                                                      |
| `SANDBOX_EXEC_TIMEOUT_SECONDS`(60) / `SANDBOX_MAX_OUTPUT_BYTES`(262144) | 执行超时与输出截断上限                                                                                             |
| `SANDBOX_REAPER_ENABLED`(true) / `SANDBOX_REAPER_INTERVAL_SECONDS`(60)  | 过期回收开关/间隔                                                                                                  |
| `SANDBOX_MSB_HOME`("")                                                  | microsandbox runtime home(空=SDK 默认 `~/.microsandbox`)                                                           |
| `SANDBOX_SHELL_IDLE_SECONDS`(1800)                                       | 持久 shell 会话空闲回收 TTL(无输入/输出/订阅满该时长即关)                                                          |
| `SANDBOX_SHELL_SWEEP_INTERVAL_SECONDS`(30)                               | 空闲回收 sweeper 周期                                                                                              |
| `SANDBOX_SHELL_CLOSED_RETENTION_SECONDS`(300)                            | 已关闭会话在内存里保留多久,供断线重连回放(之后按 404 处理)                                                          |
| `SANDBOX_SHELL_RING_BYTES`(65536)                                        | 每会话输出环形缓冲上限(最近 64KB,断线回放用)                                                                        |
| `SANDBOX_SHELL_HEARTBEAT_SECONDS`(15)                                    | SSE 心跳注释行间隔(也决定 BFF/代理的读超时下限)                                                                     |
| `SANDBOX_SHELL_MAX_PER_SANDBOX`(8)                                       | 单沙箱活跃会话上限,超出 409 `sandbox.session_limit`                                                                 |
| `SANDBOX_SHELL_MAX_SUBSCRIBERS`(32)                                      | 单会话 SSE 订阅者上限,超出 409 `sandbox.session_busy`                                                               |

### 持久 shell 会话(#2)

`POST /v1/sandboxes/{id}/exec` 是**一次性 exec**:每条命令一个独立进程,`cd`/`env`/
交互式程序(REPL、`git rebase -i`、`ssh`)**都不成立**。本服务因此提供**持久 shell
会话**:开会话 → 写 stdin → SSE 读 stdout/stderr → 关闭,会话不随 HTTP 请求结束而结束。

契约(全部在 `/v1/sandboxes/{sandbox_id}/sessions` 下):

| 方法与路径                | 语义                                                                                     |
| ------------------------- | ---------------------------------------------------------------------------------------- |
| `POST ...`               | 201 `{session_id, sandbox_id, cwd, created_at}`;body `{cwd?, cols?=120, rows?=32}`     |
| `POST .../{sid}/input`   | 204,body `{data: str}`,UTF-8 编码后写入 stdin,**单次上限 8KB**(超出 422)                 |
| `GET .../{sid}/stream`   | `text/event-stream`:先回放 ring buffer,再跟随实时输出;断开**只取消订阅**,不关会话      |
| `DELETE .../{sid}`       | 204,幂等关闭;重复关闭仍 204                                                              |

`cwd` 只接受沙箱内绝对路径(默认 `/workspace`,不能含 `..`);`cols`/`rows` 上限
500/200。沙箱状态校验与 exec 一致:未知/跨租户 404、过期 409、`stopped/failed` 409。

SSE 事件(JSON data,每帧带 `id` 供 `Last-Event-ID` 续传):

```text
id: 0
event: output
data: {"seq": 0, "data": "$ ls\r\nREADME.md\r\n"}

id: 1
event: exit
data: {"seq": 1, "exit_code": 0}

id: 2
event: error
data: {"seq": 2, "code": "sandbox.session_error", "message": "..."}
```

- `output`:`data` 是增量 UTF-8 **文本**(跨 `read()` 边界的多字节字符由增量解码器拼好;
  非法字节以 U+FFFD 替换),换行/回车原样保留(pty 下是 `\r\n`);
- `exit`:`exit_code` 为 shell 进程退出码;退出码拿不到时填 `-1`;
- `error`:`code`/`message` 描述读循环失败(provider 侧异常等);
- 心跳:空闲超过 `SANDBOX_SHELL_HEARTBEAT_SECONDS`(15s)发一行 SSE 注释(`:\n`)保活;
- 响应头固定 `Cache-Control: no-store`、`X-Accel-Buffering: no`(禁 nginx 缓冲);
- 断线重连:带上 `Last-Event-ID: <最后收到的 seq>` 即可跳过已回放部分;`seq` 单调递增,
  ring buffer 按字节淘汰(只保留最近 `SANDBOX_SHELL_RING_BYTES`)——被淘汰的历史无法回放,
  从当前可用的最早事件继续。

错误码(与沙箱既有契约一致):

| 场景                             | 错误码                      | HTTP |
| -------------------------------- | --------------------------- | ---- |
| 会话不存在 / 跨租户 / 沙箱未知   | `sandbox.not_found` / `sandbox.session_not_found` | 404  |
| 向已关闭的会话写输入             | `sandbox.session_closed`   | 409  |
| 单沙箱活跃会话超限(`SANDBOX_SHELL_MAX_PER_SANDBOX`) | `sandbox.session_limit` | 409 |
| 单会话订阅者超限(`SANDBOX_SHELL_MAX_SUBSCRIBERS`)    | `sandbox.session_busy`  | 409 |
| `Last-Event-ID` 不是合法整数     | `sandbox.bad_cursor`       | 400  |
| `cwd`/`cols`/`rows` 非法 / 输入超 8KB     | —(FastAPI 校验)            | 422  |
| provider 起不来 / 沙箱过期        | `sandbox.provider_error` / `sandbox.expired` | 502 / 409 |

两路 provider 的实现差异(**TTY 是否成立**):

- `microsandbox`:`Sandbox.exec_stream(cmd, tty=True, stdin=Stdin.pipe())` 起
  `/bin/sh -i`(失败回退 `/bin/bash -i`),**真 TTY**:pty 语义(`echo`/`isatty`/`TERM`)
  成立;stdin 通过 `handle.take_stdin()` 的 sink 写,stdout/stderr 事件合并为输出流,
  `exited` 事件带退出码;`cols/rows` 通过 `handle.resize()` 生效;
- `local`:`pty.openpty()` + `asyncio.create_subprocess_exec`(stdin/stdout/stderr 接
  slave fd、`setsid`、`TIOCSWINSZ` 设窗口大小),POSIX 上**也是真 TTY**;拿不到 pty
  设备(受限容器)/ Windows 时**自动退化为管道 shell**,`tty=False`——读写语义不变,
  但没有行缓冲/回显/终端控制序列,**不是 TTY**。local 无隔离(见下节)。

生命周期与资源回收:

- 会话表在**进程内**(键 `tenant_id/sandbox_id/session_id`),不落库;进程重启即丢;
- 空闲回收:后台 sweeper 每 `SANDBOX_SHELL_SWEEP_INTERVAL_SECONDS` 扫一遍,超过
  `SANDBOX_SHELL_IDLE_SECONDS`(30 分钟)无输入/输出/订阅的会话被关闭;
- 已关闭会话在内存里保留 `SANDBOX_SHELL_CLOSED_RETENTION_SECONDS`(5 分钟)供回放,
  之后再访问按「会话不存在」404;
- 沙箱 `DELETE` / 过期回收时联动关闭它的全部会话;进程关停(lifespan shutdown)兜底
  关闭所有会话。

**限制(务必在部署前确认)**:

- **单实例假设**:会话表是进程内字典,不做跨实例共享/粘性路由。多副本部署时,
  `POST .../sessions`、`.../input`、`.../stream`、`DELETE ...` 必须落在**同一个实例**
  (否则表现为会话不存在 404);水平扩展需要粘性会话或把会话状态外置(未实现);
- **长连接**:SSE 是长期占用的 HTTP 连接。BFF 的 httpx 读超时是**两次收数据**的间隔
  (30s),服务端 15s 心跳 < 30s,因此不会被掐断;但任何代理/网关都要关闭响应缓冲
  (`X-Accel-Buffering: no` 只对 nginx 生效,其他网关需自行配置);
- **资源**:每个会话一个 provider 侧进程 + 一个后台读任务 + 最多 32 个订阅队列;
  单沙箱最多 8 个会话。异常路径靠 sweeper/沙箱回收兜底,不做跨进程强制清理——
  provider 侧进程若在服务崩溃后残留,需要 provider 自己的生命周期(microVM TTL /
  宿主进程组)清理;
- **回放不是持久化**:ring buffer 只保留最近 64KB,且只存在于存活实例的内存里;
  断线太久或实例重启后不可回放。

### local provider(宿主执行,无隔离)

`WORKSPACE__SANDBOX_PROVIDER=local` 时,一个沙箱就是**宿主上的一个目录 + 直接拉起的
子进程**:命令以 workspace-service 进程身份在同一台机器上执行,没有 microVM、没有容器。

目录布局(根由 `WORKSPACE__SANDBOX_LOCAL_ROOT` 指定,默认 `./.sandboxes`,相对
**服务进程工作目录**;沙箱名由服务端生成,形如 `bm-` + 20 位 hex):

```text
<root>/
└── bm-1f2e3d4c5b6a79887f6e/   # 一个沙箱 = 一个目录
    ├── workspace/              # 沙箱内 /workspace 映射到这里
    ├── home/                   # 子进程的 HOME
    └── tmp/                    # 子进程的 TMPDIR
```

- exec 默认工作目录是沙箱的 `workspace/`;`cwd` 与文件路径都只接受以 `/` 开头的
  绝对路径,其中 `/workspace/...` 映射进沙箱目录(`/workspace/a` →
  `<root>/<name>/workspace/a`),路径穿越与符号链接逃逸被 400
  `sandbox.path_invalid` 挡住;其他宿主绝对路径(如 `/tmp`)按宿主语义透传——命令
  本来就是宿主进程(见下);
- 文件路径校验与 microsandbox 一致:必须以 `/workspace` 开头、不能含 `..`、防符号
  链接逃逸,违规 400 `sandbox.path_invalid`;沙箱内文件不存在 404
  `sandbox.file_not_found`;
- `DELETE /v1/sandboxes/{id}` 与过期回收 = 幂等删掉整个 `<root>/<name>` 目录
  (workspace/home/tmp 一并清空),目录本来不存在时静默成功。

**没有隔离 —— 这是本 provider 的核心限制**:

- 命令**以 workspace-service 进程身份在宿主执行**:该用户能读写的宿主文件、能访问的
  网络、能起的常驻进程,沙箱内命令同样可以;`argv` 不经 shell,超时按**进程组**
  SIGKILL(仅 POSIX,Windows 退化为单进程),但这些都不构成安全边界;
- 镜像 / CPU / 内存 / 网络策略在 local 下**无法强制执行**:请求里的
  `image`/`cpus`/`memory_mb` 不改变实际执行环境,`sandbox_network=deny` 也挡不住
  出网,它们只留在 DB 行里供审计;
- 启动时 lifespan 会打一条 warning 日志,提示命令直接在宿主执行、无隔离。

适用场景与禁用:

- **适用**:没有 microVM runtime 的本地开发(零运行时依赖跑通 stdio MCP / skill 执行
  链路)、单租户可信环境、桌面应用「本地电脑模式」;
- **禁用**:**多租户 / 生产**。local 下不可信代码等同在宿主机上执行;生产继续用默认的
  `microsandbox`(要求可写 `SANDBOX_MSB_HOME`,且 runtime 能访问 OCI registry)。

服务目录 `.env` 示例(`services/*/.sandboxes/` 已在 `.gitignore` 中):

```sh
WORKSPACE__SANDBOX_PROVIDER=local
# local provider 的宿主根目录:相对服务进程工作目录,也可给绝对路径
WORKSPACE__SANDBOX_LOCAL_ROOT=./.sandboxes
```

错误码沿用上面的表:local 下会出现 `sandbox.path_invalid`(400)、
`sandbox.file_not_found`(404)、`sandbox.exec_timeout`(502)与
`sandbox.provider_error`(502,本地目录已存在、创建/删除目录失败、沙箱名不合法);
`sandbox.provider_unavailable` 只属于 `microsandbox`/`unavailable`,不会出现在 local。

### 无 runtime / 部署注意

- 没有 microsandbox runtime 或 SDK 未安装时,创建沙箱返回明确的
  `502 sandbox.provider_unavailable`,**不提供"看起来能用"的空接口**;需要在没有
  runtime 的机器上跑通执行链路时,改用 `provider=local`(无隔离,见上);
- 部署时 `MSB_HOME`(或 `WORKSPACE__SANDBOX_MSB_HOME`)必须指向**可写目录**
  ——默认的 `~/.microsandbox` 在容器/受限环境里往往不可写;
- 首次创建需要拉 OCI 镜像,runtime 必须能访问 registry(如 index.docker.io);
  网络策略 `deny` 只限制**沙箱内**出网,不影响宿主侧拉镜像。

测试(零网络、零 runtime):`tests/conftest.py` 的 `FakeSandboxProvider`
注入内存文件表与固定执行输出,`create_app(..., sandbox_provider=fake)` 覆盖
生命周期/隔离/路径校验/过期回收/故障收口,不会启动真实 microVM。

命令(仓库根):`npx nx run workspace-service:serve`(:8005)/ `npx nx test workspace-service`
迁移:`cd services/workspace-service && uv run alembic upgrade head`
