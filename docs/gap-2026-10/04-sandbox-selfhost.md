# M1-00 前置研究：沙箱可否自建服务（2026-10-18）

> 问题：beauty-moon 的沙箱（`docs/feature-gap.md` M1-00/M1-01）是接托管服务还是自建？
> 结论先行：**可以自建，但"自建"有两条完全不同的路线，不要混为一谈**：
> - **路线 A（推荐起步）：自建"沙箱服务"，不自建"隔离内核"** —— 引擎用开源（microsandbox / E2B infra / gVisor），自己写的是 provider 抽象 + 生命周期/租户/配额那层。这层本来就是 beauty-moon 的业务，托管服务替代不了。
> - **路线 B（不建议本阶段）：自建隔离内核**（自维护 gVisor/Firecracker runner、快照、调度、网络策略）——是 L 级长期投入，需要专门团队。
>
> 与 `03-runtime-platform.md` §1 的结论一致并细化：**先做 provider 抽象（S），spike 用 microsandbox（本地零成本）与 E2B/Fly（云侧）各验证一遍，再决定引擎**。

---

## 1. 候选引擎盘点（自建视角）

| 引擎 | 隔离级别 | 开源/许可 | 自建可行性 | 关键事实 |
| --- | --- | --- | --- | --- |
| **microsandbox** | microVM（每沙箱独立内核） | Apache-2.0（YC F26） | ★★★ 本阶段最可行 | "run locally, no daemon/root/account"，`npx microsandbox run ubuntu:latest` 一条命令起本地 microVM；同一 SDK 可切它家云/BYOC；明确面向多租户场景（每租户任务一个内核、egress allowlist、凭据占位符替换）；有 Rust/TS/Python/Go/Ruby SDK 与快照支持。来源：[microsandbox.dev](https://microsandbox.dev/)、[multi-tenant 用例页](https://microsandbox.dev/use-cases/multi-tenant) |
| **E2B infra** | Firecracker microVM | 开源（Apache-2.0，仓库 e2b-dev/infra） | ★★ 可自托管但门槛高 | 官方提供 self-host 路径（GCP 可用 / AWS beta）；Terraform 起一整套（orchestrator、FN、stealthsubset 等），运维复杂度高于 microsandbox；社区有第三方复刻实践。来源：[E2B Enterprise](https://e2b.dev/enterprise)、[e2b-dev/infra](https://github.com/e2b-dev/infra)（self-host 文档随版本变动，接入前以仓库现状为准——本次抓取 self-host.md 已 404，属待核实项） |
| **gVisor（runsc）** | 用户态内核（syscall 拦截） | Apache-2.0（Google） | ★★ 适合容器栈 | 无需 KVM/嵌套虚拟化，直接套在 runc 位置；腾讯有百万级 agentic sandbox 实践；性能开销视 workload 而定（syscall 密集型明显）。来源：[gvisor.dev](https://gvisor.dev/docs/)、[腾讯实践 blog](https://github.com/google/gvisor/blob/master/website/blog/2026-04-23-scaling-agentic-rl-sandboxes-to-the-millions-with-gvisor-at-tencent.md) |
| **Firecracker（裸用）** | KVM microVM | Apache-2.0（AWS） | ★ 需要较多底层工作 | 要求 KVM/嵌套虚拟化（云厂商 VM 上不一定开放）；K8s 社区 agent-sandbox 有用例。beauty-moon 当前部署形态（Fly/云 PG/无 K8s）下直接运维成本最高。来源：[Firecracker](https://firecracker-microvm.github.io/)、[agent-sandbox 用例](https://agent-sandbox.sigs.k8s.io/docs/use-cases/examples/firecracker-sandbox/) |
| **纯 Docker 容器** | namespace/cgroup 共享内核 | 开源 | ★ 不满足多租户威胁模型 | 容器逃逸面大；对"跑用户/agent 生成的代码"这种不可信负载，业界共识是内核级隔离（E2B/microsandbox/OpenHands 均不用裸容器做对外执行）。 |

## 2. 判定：自建 = 写哪一层

```
orchestrator(code_tools 改造)
   └── SandboxProvider 接口（自建服务，beauty-moon 自己写）★核心资产
        ├── create(tenant, image, ttl) → sandbox_id
        ├── exec(cmd) / write_file / read_file
        ├── snapshot / restore
        ├── destroy / 回收 job
        └── 实现A: microsandbox SDK    实现B: E2B     实现C: Fly Machines（直接容器/VM）
```

- **必须自建（无可替代，M 规模）**：provider 抽象、沙箱实例表（租户/配额/TTL/状态机）、生命周期回收 job、egress 策略与凭据注入、与 run 状态机和事件流的对接、审批策略联动（`sandbox_required` 工具声明）。
- **可以借力（S-M 规模）**：隔离引擎本身（上表）、镜像构建流水线（E2B 的 custom templates / microsandbox 的 OCI 镜像）。
- **不要自建（L 规模，除非有硬合规）**：Firecracker/gVisor 的调度、快照、网络栈运维——除非出现"数据绝不能出内网"的硬需求（那是 M0-13 内网形态，当前已暂缓）。

## 3. 建议路径（对齐 M1-00/M1-01）

| 步骤 | 内容 | 规模 |
| --- | --- | --- |
| 1 | 定 `SandboxProvider` 接口 + 沙箱实例表 + `code_tools` 声明 `sandbox_required`（本地实现先用受限 roots 只读版兜底，即现状） | S |
| 2 | spike A：microsandbox 本地/单机（一条命令、Apache-2.0、SDK 齐），验证 exec/文件/快照/回收全链路 | S |
| 3 | spike B：云侧二选一（E2B 自托管 vs Fly Machines），同样接口跑通；对比成本（`03` §1 的计费形状结论：低利用率下 active/实际用量计费更省） | S |
| 4 | 按两个 spike 的结果拍板 M1-00，写决策记录（把本文 §1 表收进去） | S |
| 5 | 生产化：回收 job、并发上限、租户配额、审计事件（`sandbox.created/called/destroyed.v1`） | M |

**风险与待核实**：
- microsandbox 的 cloud 仍是 private beta（自建用 local/BYOC 不受影响）；引擎成熟度与社区规模低于 E2B/gVisor，spike 时需压测稳定性。
- E2B 自托管文档随版本变动（self-host.md 本次抓取已 404），AWS 支持仍 beta——以仓库现状复核后再承诺。
- gVisor 性能开销因 workload 而异，若 beauty-moon 工具面以"跑测试/构建"为主需先测。
- Firecracker 需 KVM/嵌套虚拟化，目标部署环境（Fly VM 内）是否可用需单独验证。

**一句话**：沙箱**服务**应当自建（provider 抽象 + 生命周期 + 租户配额是业务资产）；沙箱**引擎**用开源（首选 microsandbox 或 E2B infra，容器栈可选 gVisor），不要自己维护 microVM 基础设施。

---

## 2026-10-20 实施记录（沙箱 P1：microsandbox 路线）

> 决策：引擎先 **microsandbox**（0.7.8，Python SDK，Apache-2.0，microVM）；托管 HTTP MCP 暂留
> orchestrator + SSRF 加固；skill 兼容 SKILL.md 包；沙箱按对话级常驻（TTL 滑动）；允许
> 沙箱内 `npx`/`uvx` 联网安装（后续用出网 allowlist 收口）。

**P1 已交付（本次）**

- `services/workspace-service` 新增沙箱模块：
  - `SandboxProvider` 协议 + `MicrosandboxProvider`（detached 创建、按名重连执行、
    `Network.none/allow_all`、cpus int / memory MiB、`max_duration/idle_timeout` TTL 兜底、
    输出截断、SDK 异常统一收口为 `UpstreamError`）+ `UnavailableSandboxProvider` 工厂；
  - `sandboxes` 表（tenant/name 唯一/kind/provider/image/cpus/memory/network/status/
    expires_at/last_used_at/last_error/meta）+ alembic 迁移 `c4d8e6f2a9b1`；
  - 内部 API：`POST /v1/sandboxes`、`GET /{id}`、`POST /{id}/exec`、`PUT/GET /{id}/files`、
    `POST /{id}/touch`、`DELETE /{id}`；`/workspace` 路径白名单（拒绝 `..` 与 NUL）；
  - 生命周期：创建先落库、失败保留 failed 记录；exec 前校验状态/过期；成功滑动续期；
    reaper 后台任务回收过期实例（失败留 `last_error`，不阻塞其他实例）。
  - 测试：Fake provider（零网络）+ 假 SDK 适配测试，共 49 passed；`alembic check` 无漂移。
- `services/agent-orchestrator` 新增 `SandboxClient`（create/get/exec/write_file/read_file/
  touch/destroy），`sandbox_enabled` 默认关闭，P1 不向模型暴露工具；12 passed。
- 环境要求：`WORKSPACE__SANDBOX_MSB_HOME` 指向可写目录（默认 `~/.microsandbox` 在受限
  容器/沙箱里不可写）；拉镜像需要 OCI registry 出网。当前开发机因 DSH 文件/网络策略
  只验证到 provider 适配层，未启动真实 microVM。

**阶段状态**

| 阶段 | 状态 | 内容 |
| --- | --- | --- |
| P2 | ✅ 已落地（2026-10-20） | stdio MCP：`MCPServer` 增加 transport/command/args/env/runtime/image、沙箱内启动与 JSON-RPC 代理、测试端点、Web 表单 |
| P3 | ✅ 已落地（2026-10-20） | 用户上传 SKILL.md 包：对象存储 + 版本表 + 上传校验 + run 固定版本 + 沙箱执行 + 交付物回传 |
| P4 | 待做 | 出网 allowlist/凭据注入、配额与 `sandboxMs` 计量、审计事件、快照/预热、E2B/Fly provider |

### P2 实施记录（stdio MCP）

- **orchestrator**（`services/mcp_stdio.py`）：
  - `StdioSandboxPool` 按 key 常驻沙箱（key = `<conversation_id | run:<id> | anonymous>:<server_id>`），
    每 key 一把 `asyncio.Lock`，`touch` 失败即重建；probe 只写一次；
  - `PROBE_PY` / `PROBE_JS` 是沙箱内的**一次性** MCP 客户端：读 stdin 一行 JSON →
    spawn 命令 → `initialize` → `notifications/initialized` → `tools/list` / `tools/call` →
    输出 `BEAUTY_MCP_RESULT:{...}`；只用标准库，超时杀进程，stderr 尾随错误回传；
  - `MCPServer` 新列 `transport/command/args/encrypted_env/runtime/image`（迁移
    `a8f3c1d6e2b4`）；命令必须命中 allowlist，`env` 整体加密、读端只回 `has_env/env_keys`；
  - `ToolContext`（contextvars）在 run 的 4 个 `ainvoke` 边界与 chat 工具路径绑定，
    工具 handler 用它选沙箱作用域——**同一对话复用、不同对话隔离**；
  - 测试 23 个新用例（含本地 `PROBE_PY` + dummy stdio server 的真实进程测试），
    全量 orchestrator **196 passed**；alembic head=`a8f3c1d6e2b4` 无漂移。
- **Web**（`apps/web`）：MCP 面板新增 HTTP/stdio 切换、stdio 表单
  （command/args/env/runtime/image）、列表显示 transport/command 与 `env_keys` 个数；
  typecheck / 219 tests / lint 全绿。
- **已知边界**：probe 是"一次调用一个 MCP 子进程"（npx 冷启动开销，P4 可换常驻
  process bridge）；沙箱池是 orchestrator 进程内缓存（多实例不复用，P4 落 DB）；
  P2 出网 `public` 走 microsandbox `NetworkProfile.PUBLIC`（**只放公网，不含宿主/私网**），
  但仍是"任意公网"而非 allowlist，**P4 前不要向公网租户开放**；probe 只能杀直接子进程，
  孙进程靠沙箱 TTL 兜底。

### P3 实施记录（用户上传技能包）

- **存储**：workspace-service 新增通用对象 API（`POST/GET/DELETE /v1/objects`，
  base64 + sha256，key 租户前缀强校验，默认 20MB 上限）；zip 本体进对象存储，
  orchestrator 的 `skill_packages`/`skill_versions` 只存引用 + manifest/SKILL.md 快照。
- **上传/校验**（`services/skill_packages.py`，纯 stdlib）：20MB / 500 文件 / 解压 50MB、
  路径穿越/绝对路径/符号链接/NUL/重复路径全部拒绝；允许单一顶层目录；`skill.json`
  可选（entrypoint/runtime/network/timeout/tools）；有 entrypoint 自动补 `skill_run` 工具。
- **版本化**：租户内 slug + version 不可变；新版本不影响已建 run；内置技能 id 冲突 409。
- **run 快照**：runs 新增 `skill_version_id/object_key/sha256/entrypoint/runtime/network/
  timeout_seconds`（迁移 `e4c8b1f7a2d9`），创建 run 时冻结，后续上传不改变语义。
- **执行**（`services/skill_runner.py`）：`skill_run` 工具（确认闸）→ 一次性沙箱
  （kind=skill）→ 取包并校验 sha256 → 沙箱内 `python3` 解包（再校验路径/符号链接/规模）→
  执行 entrypoint（cwd=/workspace/skill，stdin 传 input/goal/title）→ 读 `/workspace/output`
  文本产物 → 登记 run 交付物 → finally 销毁沙箱；chat 路径无 run_id 不暴露该工具。
- **Web**：Skills 页支持上传 zip、来源/含脚本徽标、版本展开与删除；FormData 上传。
- **测试**：workspace-service **60 passed**、orchestrator **264 passed**、web **226 passed**；
  alembic head=`e4c8b1f7a2d9` 无漂移；另做了 orchestrator→workspace 对象 API 的
  ASGI 真实契约联调（上传→合并列表→创建 run→快照/对象 sha 校验）。
- **已知边界**：node runtime 的解包/产物清单脚本仍是 Python，要求镜像含 `python3`；
  run_artifacts 每 run 一件（文本产物合并登记，交付阶段覆盖）；
  真实 microVM 未在本机验证（环境限制）；P4 需补 egress allowlist/凭据/计量。

**风险**：microsandbox 云侧仍 beta；本地 runtime 依赖宿主虚拟化能力；镜像拉取与
`npx` 安装需要可控出网——P4 前必须把 egress 策略与包校验补上。
