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
