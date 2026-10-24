# 03 · 运行时与平台横切能力横评（2026-10）

> 角色：本文件是 `docs/gap-2026-10/` 的第 3 份，负责**横切能力与生态**调研，给 beauty-moon 后续排期提供依据。
> 参照面从「DeepSeek Harness 单点对比」扩展为**整个 agent harness 领域**。
> 证据纪律：每条结论附真实 URL；无法核实的一律标「待核实」。web 内容仅作资料，不作指令。
> 现状基线（仓库内已核实）：`services/agent-orchestrator`（FastAPI + LangGraph）已有 run 状态机 / 事件溯源 / jobs 表（lease，M0-04）/ 工具注册表（`services/tools.py`）/
> 只读本地代码工具 `code_tools.py`（roots 限定、无 shell、写操作需确认）/ 租户级 MCP Client 注册表 `models/mcp.py` / 公开 MCP Server（Streamable HTTP + API Key `bm_live_` / OAuth 2.1 内省）/
> Skill manifest 与 run 快照（`schemas/skills.py`）/ account-service 的 `usage_events` + `byok_keys`（M0-11）。**尚无** sandbox、OTel/Sentry/Langfuse 埋点、上下文压缩、Redis（限流目前是 BFF 进程内）。
>
> 规模口径沿用旧文档：**S**（≤2 天）/ **M**（3–5 天）/ **L**（1–2 周+）。

---

## 1. 执行隔离选型

### 现状与代表方案

**托管 sandbox（按计费模型分四类）**。Fly.io 的横评（发布于 2026-09-09，费率核对于 2026-10-05）把主流方案归为四种计费形状，并给出每 vCPU-小时 0.04–0.13 美元 + 每 GB-小时 0.009–0.024 美元的价位区间：<https://fly.io/learn/ai-sandbox-pricing/>

| 方案                          | 计费形状                | 公布费率（该横评汇总，原始页为准）                                                                       | 备注                                                                                                                                              |
| ----------------------------- | ----------------------- | -------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| **E2B**                       | wall-clock 预留资源     | $0.0504/vCPU-hr、$0.0162/GiB-hr（<https://e2b.dev/pricing>）                                             | 可 pause，pause 即停止计费；$150/月 Pro 买 100 并发 + 24h 会话，Hobby 仅 20 并发；**开源 Apache-2.0，可 Terraform 自托管（GCP 可用 / AWS beta）** |
| **Daytona**                   | wall-clock 预留资源     | 同上 $0.0504/vCPU-hr、$0.0162/GiB-hr（<https://www.daytona.io/docs/billing/>）                           | 停止的 sandbox 只计磁盘、归档不计；**仓库 2026-06 声明核心开发迁往私有代码库**（横评 [12]），开源走向待核实                                       |
| **Modal Sandboxes**           | 请求量与实测取大者      | $0.1419/physical core-hr（≈2 vCPU）、$0.024/GiB-hr（<https://modal.com/pricing>）                        | 单价最高，强项在 GPU；无 GPU 需求时不划算                                                                                                         |
| **Vercel Sandbox**            | Active CPU 计费         | $0.128/Active CPU-hr、$0.0212/GB-hr **预留** 内存、1 分钟起（<https://vercel.com/docs/sandbox/pricing>） | 等模型调用不计 CPU，但内存一直计；2GB/vCPU 固定配比                                                                                               |
| **Cloudflare Sandbox**        | Active CPU + 固定 shape | $0.072/vCPU-hr(active)、$0.009/GiB-hr（Workers Paid $5 含 375 vCPU-min）                                 | 按 Cloudflare Containers 计价                                                                                                                     |
| **Fly.io Sprites / Machines** | 实测用量                | $0.0385/CPU-hr（实际消耗）、$0.021875/GB-hr（实际用量）（<https://fly.io/learn/ai-sandbox-pricing/>）    | running/warm/cold 三态，只有 running 计 CPU；单价最低                                                                                             |

**自建底层隔离**：gVisor（用户态内核，腾讯有百万级 agentic-RL sandbox 实践：<https://github.com/google/gvisor/blob/master/website/blog/2026-04-23-scaling-agentic-rl-sandboxes-to-the-millions-with-gvisor-at-tencent.md>）、Firecracker microVM（Kubernetes SIG 的 agent-sandbox 有 Firecracker 用例：<https://agent-sandbox.sigs.k8s.io/docs/use-cases/examples/firecracker-sandbox/>）。**只读方案**＝不给执行环境，只给只读文件/代码分析与补丁建议——beauty-moon 今天的 `code_tools.py` 已经是这一档（roots 限定 + 路径逃逸拦截 + 无 shell + 写操作需确认，见 `services/agent-orchestrator/agent_orchestrator/services/code_tools.py`）。

关键洞察（该横评）：agent 大量时间在等模型调用，因此**按 active/实际用量计费者在低利用率下更便宜；wall-clock 计费者恒定**。100 sandbox-hours（2vCPU/4GB）算例：25% CPU 忙时 E2B/Daytona $16.56、Fly Sprites $5.21；100% 忙时 E2B $16.56、Fly $16.45。规划费通常是账单主体（E2B Pro $150/月 vs 几百次会话的算力费）。

### 对 beauty-moon 的建议

1. **采纳（立即，S）**：把已有 `code_tools.py` 的**只读/确认式**路径产品化，作为 M1 前的对外承诺（不承诺执行）。这把 `docs/archive/2026-10/feature-gap.md` 的「M1-00 未拍板就不承诺执行」落成可交付能力，且零外部依赖。
2. **采纳（M1-00 spike，S）**：托管 sandbox 二选一对比 **E2B** 与 **Fly.io Sprites/Machines**。理由：都是「暂停/三态即停止计费」的模式，契合等模型的空闲特性；Fly 单价更低且与 `docs/archive/2026-10/feature-gap.md` 既定的 Fly 部署栈一致，E2B 是 agent 领域事实标准 + 可自托管（为将来 M0-13 内网/离线留后路）。
3. **暂缓**：Modal（无 GPU 需求时不经济）、Cloudflare Sandbox（与 Vercel/Fly 双端栈冲突，新增第三套运行时）。
4. **暂缓/倾向不做**：自建 gVisor/Firecracker runner。它有成熟实践，但需要专门团队运维内核/快照/调度，属 L 级长期投入；除非出现强合规（数据不出网）硬需求，否则先用托管并在 M0-13 用 E2B 自托管版本兜底。
5. **风险对冲**：Daytona 开源核心转私有（2026-06 公告）说明该赛道供应商稳定性有风险 → 接入层必须做 **sandbox provider 抽象**（一次 run 一个 sandbox id + 统一生命周期 API），避免绑死。

### 接入代价

| 事项                                    | 规模                                                                  | 说明                                |
| --------------------------------------- | --------------------------------------------------------------------- | ----------------------------------- |
| 只读代码能力产品化（沿用 code_tools）   | **S**                                                                 | 已有实现，补 workspace 抽象与 UI    |
| E2B / Fly Sprites spike + provider 抽象 | **S**（spike）+ **M**（接入：生命周期、文件同步、TTL 回收、并发上限） | 需要新表记录 sandbox 实例与租户配额 |
| 自建 gVisor/Firecracker runner          | **L**                                                                 | 不建议本阶段                        |

**待核实**：各厂商费率（第三方横评核对于 2026-10-05，原始定价页随时变）；E2B 自托管在 AWS 仍为 beta；Daytona 私有化后的开源承诺走向。

## 2. 子代理与多 agent 编排

### 现状与代表方案

| 方案                                | 形态                                                           | 对多租户 SaaS 的适配点                                        | 来源                                                                       |
| ----------------------------------- | -------------------------------------------------------------- | ------------------------------------------------------------- | -------------------------------------------------------------------------- |
| **Claude Agent SDK subagents**      | 主代理通过 Task 工具把子任务交给独立上下文的子代理，只回传结论 | 子代理天然是「收窄上下文 + 收窄工具」的资源单位，不需要新框架 | [Claude Agent SDK overview](https://code.claude.com/docs/en/agent-sdk)     |
| **OpenAI Agents SDK handoffs**      | 以 handoff 在多个 agent 间转移控制权，配合 guardrails          | handoff 语义清晰、易测；状态仍在单进程内存                    | [OpenAI Agents SDK](https://openai.github.io/openai-agents-python/)        |
| **LangGraph**（beauty-moon 已在用） | 图编排 + checkpointer，可用 Send API 做 fan-out                | 已有 checkpointer 与事件溯源可复用，引入成本最低              | [LangGraph](https://langchain-ai.github.io/langgraph/)                     |
| **Temporal / Inngest**              | 持久化工作流引擎，天然重试/补偿/长任务                         | 解决「进程重启不丢进度」，但引入第二套运行时与运维成本        | [Temporal](https://temporal.io/) · [Inngest](https://www.inngest.com/docs) |

### 对 beauty-moon 的建议

1. **采纳（P1，M）**：先做同租户内的**子 run**——复用现有 run 状态机，加 `parent_run_id`，继承租户/配额/审批策略，事件流按父子可追溯。等价于 Claude Code 的 Task 子代理，但不引入新框架，天然受事件溯源与 SSE 观测。
2. **暂缓（P2）**：不引入 Temporal/Inngest。现有 jobs 表 + lease + checkpoint 覆盖了大部分场景；等「跨天等待外部回调」成为真实瓶颈、且 PG checkpointer 与多实例 worker 都稳定后再评估。
3. **不做**：通用多 agent「任务板/消息总线/写域」形态——对 SaaS 客户价值远低于「单 run 更可靠、更便宜」。

### 接入代价

| 事项                                                        | 规模  | 说明                  |
| ----------------------------------------------------------- | ----- | --------------------- |
| 子 run（parent_run_id + 工具子集 + 事件父子关系 + UI 折叠） | **M** | 无新依赖              |
| 并行 fan-out（LangGraph Send + 并发上限 + 配额扣减）        | **M** | 依赖子 run 与配额执行 |

**待核实**：Claude Agent SDK 子代理的并发上限与上下文隔离粒度；Agents SDK handoff 的状态持久化方案。

## 3. 上下文与记忆

### 现状与代表方案

- **自动压缩 / context editing**：通行做法是接近窗口阈值时把早期轮次替换为摘要，保留最近 N 轮原文与「不可丢弃」的结构化事实（任务目标、验收标准、用户偏好），部分产品另提供显式命令。[有效上下文工程 · Anthropic](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- **记忆层**：mem0 / Zep / Letta 提供「事实抽取 → 存储 → 检索注入」的长期记忆，均为额外服务，需评估数据驻留与租户隔离。[mem0](https://docs.mem0.ai/) · [Zep](https://help.getzep.com/) · [Letta](https://docs.letta.com/)
- **成本结构**：长会话成本主要在「每轮重放全量历史」，压缩的价值不仅是防超窗，更是把成本从线性拉回可控。

### 对 beauty-moon 的建议

1. **采纳（P0，M）**：先做 **token 计量 + 预算**（prompt / 工具输出 / 上下文分别计），超预算走压缩而不是静默截断——这是后续所有上下文工作的前置，也直接喂给 §8 的用量计量。
2. **采纳（P1，M）**：把压缩实现成**事件投影**：从事件流生成 `context.compacted.v1` 摘要事件，而不是就地改写历史；压缩因此可回放、可评测、可 A/B。
3. **暂缓（P2）**：接入 mem0/Zep 这类记忆服务；先用「租户/工作区级约定文件」（对应 AGENTS.md / CLAUDE.md 模式）覆盖 80% 需求。

### 接入代价

| 事项                                                                | 规模    | 说明                                                      |
| ------------------------------------------------------------------- | ------- | --------------------------------------------------------- |
| token 计量与预算（gateway 回传 usage → orchestrator 记账 → 落事件） | **S-M** | 网关已返回 usage，当前被丢弃（`services/llm.py:125-133`） |
| 压缩投影（摘要节点 + 阈值配置 + UI 提示）                           | **M**   | 复用现有图节点                                            |
| 租户级约定文件（DB 存储 + 注入 system prompt + UI 编辑）            | **S**   | 参考 AGENTS.md 形态                                       |

**待核实**：各家自动压缩的默认阈值（公开资料未给出统一数值，不臆造）。

## 4. 工具与扩展生态（MCP / skills / hooks）

### 现状与代表方案

- **MCP 已是事实标准**：工具/资源/提示以协议接入，客户端与服务端双向可用；官方有 registry 与规范站，生态扩张主要靠 server 数量与 SDK 成熟度。[MCP 规范](https://modelcontextprotocol.io/) · [MCP Registry](https://registry.modelcontextprotocol.io/)
- **Agent Skills（技能）**：以「目录 + SKILL.md + 附带的脚本/资源」为打包单位，按需加载，避免把所有说明塞进 system prompt。[Agent Skills 说明 · Anthropic](https://www.anthropic.com/news/skills)
- **Hooks（生命周期钩子）**：在工具调用前后、会话开始结束等节点插入确定性逻辑（校验、脱敏、审计、拦截），是「策略强制」而非「提示约束」。[Claude Code hooks](https://code.claude.com/docs/en/hooks)
- **插件市场**：主要由各家客户端各自定义，目前没有跨产品统一标准。

### 对 beauty-moon 的建议

1. **采纳（P1，M）**：先做 **hooks**（`pre_tool` / `post_tool` / `run_start` / `run_end`），把「参数校验、敏感信息脱敏、审计落库」从代码里抽成可配置点——这是审批策略引擎（§7）的落地载体。
2. **采纳（P1，M）**：Skills 从仓库文件升级为**租户级 DB 记录**（版本 + 启停 + 工具白名单 + 交付物定义），并让 manifest 里已有的 `permissions` 真正参与审批裁决。
3. **暂缓（P2）**：Skill 市场/插件市场。先内部可用，等出现外部开发者需求再做分发与审核。
4. **不做**：自建 MCP registry；接入官方 registry 索引即可。

### 接入代价

| 事项                                           | 规模  | 说明                    |
| ---------------------------------------------- | ----- | ----------------------- |
| hooks 注册表 + 内置三个 hook（审计/脱敏/校验） | **M** | 事件流已可承载审计      |
| Skills DB 化 + 版本 + manifest 生效            | **M** | 涉及迁移与 run 快照字段 |

**待核实**：MCP registry 是否已提供稳定的公开查询 API 与鉴权模型。

## 5. 评测与可观测

### 现状与代表方案

- **LLM 可观测**：Langfuse / LangSmith / Braintrust 提供轨迹采集、prompt 版本、成本与评分；核心数据形状都是「trace → span → generation」。[Langfuse](https://langfuse.com/docs) · [LangSmith](https://docs.smith.langchain.com/) · [Braintrust](https://www.braintrust.dev/docs)
- **标准化遥测**：OpenTelemetry 的 GenAI semantic conventions 定义了模型调用、token 用量、工具调用的属性名，按此埋点可避免绑定单一厂商。[OTel GenAI semconv](https://opentelemetry.io/docs/specs/semconv/gen-ai/)
- **回归评测**：业界普遍先用「黄金任务集 + 规则/LLM 评分 + 每次改动跑一遍」的最小闭环，再谈在线评分与人工标注。

### 对 beauty-moon 的建议

1. **采纳（P0，M）**：最小可用 = **traceId 贯穿**（BFF → orchestrator → gateway）+ **usage 落库**（网关已返回 usage 但被丢弃）+ **错误上报**（Sentry 前端与 Python SDK）。这三件套直接解锁排障、计费与评测。
2. **采纳（P1，M）**：事件流 → 20 条黄金 run 的离线回归（固定桩模型或低温度），作为 CI 门禁的前置；先不上 Langfuse，等自建事件表不够用时再接。
3. **暂缓（P2）**：Langfuse/LangSmith 托管服务（数据驻留与成本待评估）；Braintrust 同理。

### 接入代价

| 事项                                     | 规模  | 说明                         |
| ---------------------------------------- | ----- | ---------------------------- |
| traceId + Sentry（web / 7 服务）         | **S** | 统一在 `common` 里封装       |
| usage 落库链路（gateway → usage_events） | **S** | 端点已存在，缺生产者         |
| 黄金 run 回归                            | **M** | 需要可复现的桩模型与事件比对 |

**待核实**：OTel GenAI semconv 是否已稳定（实验性属性可能改名）。

## 6. 浏览器与 computer use

### 现状与代表方案

- **Playwright 驱动型浏览器 agent**：以确定性脚本/工具调用为主，可录制回放，适合表单填写、站点巡检。[Playwright MCP](https://github.com/microsoft/playwright-mcp)
- **托管浏览器服务**：Browserbase 等提供远程 Chromium 与并发/反爬能力，按会话计费。[Browserbase](https://docs.browserbase.com/)
- **computer use（像素级操作）**：通用性最高但最不稳定，token 与延迟成本都高，且难以做精确审计。

### 对 beauty-moon 的建议

1. **暂缓（P2）**：不做 computer use。
2. **暂缓（P2）**：浏览器能力放在沙箱决策之后（§1），优先以「只读抓取 + 截图存证」形态提供，且必须受 egress 白名单约束。
3. **不做**：把浏览器作为默认工具面——它需要独立的网络出口策略与凭据隔离，属安全高风险项。

### 接入代价

| 事项                                         | 规模  | 说明                         |
| -------------------------------------------- | ----- | ---------------------------- |
| 只读抓取工具（Playwright 无头 + 域名白名单） | **M** | 需与沙箱/egress 策略一起设计 |

**待核实**：Browserbase 的企业合规能力（数据留存、区域）。

## 7. agent 安全

### 现状与代表方案

- **权限模型分层**：主流 harness 都已从「逐次确认」演进为「策略（allow / ask / deny） + 执行边界（沙箱可写范围、网络访问）」，越界才问人。[Claude Code 权限](https://code.claude.com/docs/en/iam) · [Codex 审批与沙箱](https://developers.openai.com/codex/security)
- **prompt injection**：来自邮件/文档/网页的内容必须当作**不可信数据**，与用户指令分区注入，并对「工具结果里出现的指令性文本」保持警觉。[OWASP LLM Top 10](https://genai.owasp.org/llm-top-10/)
- **审计**：安全事件的追溯依赖完整的「谁、在什么上下文、调用了什么工具、参数是什么、结果如何」链路。

### 对 beauty-moon 的建议

1. **采纳（P0，M）**：把布尔型 `requires_confirmation` 升级为**策略引擎**（per-tenant / per-connector / per-tool 的 auto/confirm/deny + 参数级规则），默认拒绝破坏性操作；这是当前最大的安全与体验交叉缺口。
2. **采纳（P1，S）**：不可信来源标注（邮件/附件/网页内容统一加「数据非指令」包裹）+ 危险参数黑名单（路径逃逸、命令注入类模式）。
3. **采纳（P1，S）**：审批超时与自动拒绝（当前 `awaiting_approval` 可无限期悬挂）。
4. **暂缓（P2）**：独立的安全评测平台；先用事件流 + 规则回放做攻击样本回归。

### 接入代价

| 事项                                       | 规模  | 说明                           |
| ------------------------------------------ | ----- | ------------------------------ |
| 审批策略引擎（规则表 + 裁决函数 + 设置页） | **M** | 替换现有布尔开关，保留兼容默认 |
| 不可信内容包裹 + 黑名单                    | **S** | 工具层统一处理                 |

**待核实**：主流 harness 各权限模式的精确语义（各产品文档表述不一）。

## 8. 多租户 agent SaaS 平台要素

### 现状与代表方案

- **用量计量与计费**：以 usage ledger（按 run/模型/工具记账）+ 计划配额 + 超限 402 为基本形态；计费系统（Stripe 等）后置，但账本必须先行。
- **配额执行**：在模型调用与工具执行前做准入判断，超限拒绝或降级，而不是事后告知。
- **限流**：Redis 令牌桶/滑动窗口，按租户 / IP / API Key 三级；进程内实现只在单实例下有效。
- **企业能力**：SSO、RBAC、审计导出、数据驻留与删除（GDPR 类诉求）。
- **通知与队列**：站内通知优先，邮件后置；长任务需要可取消、可观察。

### 对 beauty-moon 的建议（按依赖顺序）

1. **采纳（P0，M）**：**usage 生产者闭环**——gateway 返回的 usage 记账到 `usage_events`，run 级汇总可见。这是计费、配额、评测、成本归因的共同底座。
2. **采纳（P0，S）**：**quota 准入判断**在 orchestrator 建 run 与模型调用前执行（当前 `organizations.quota` 全仓无读取点）。
3. **采纳（P1，M）**：限流迁到 Redis（compose 已起却未连接），按租户/IP/Key 三级；保留进程内降级。
4. **暂缓（P2）**：Stripe 自助支付、企业 SSO/RBAC/审计导出（先 Free/邀请制）。

### 接入代价

| 事项                  | 规模  | 说明                           |
| --------------------- | ----- | ------------------------------ |
| usage 生产者 + 汇总页 | **M** | 端点与表已存在                 |
| quota 准入            | **S** | 读配额 + 402 契约              |
| Redis 限流            | **M** | 需处理不可用降级与多实例一致性 |

**待核实**：企业客户对数据驻留的具体合规要求（按目标行业确认）。

## 9. 风险与不确定项

| #   | 风险 / 不确定项                                                      | 影响                 | 处置                                     |
| --- | -------------------------------------------------------------------- | -------------------- | ---------------------------------------- |
| R1  | 沙箱供应商稳定性（Daytona 开源核心转私有；E2B 自托管在 AWS 仍 beta） | 执行能力被供应商绑定 | 接入层做 provider 抽象；保留只读降级路径 |
| R2  | 各厂商定价随时变（§1 中费率来自第三方横评）                          | 成本模型失真         | 上线前以官方定价页复核                   |
| R3  | 公开资料缺少压缩阈值、权限模式档位等精确语义                         | 对标结论可能偏差     | 文中已标「待核实」，不据此做承诺         |
| R4  | OTel GenAI semconv 仍可能改名                                        | 埋点返工             | 先在内部封装一层属性映射                 |
| R5  | 无 CI 门禁，改动回归靠人工                                           | 平台化后回归成本陡增 | 与 §5 黄金 run 回归一并落地              |
| R6  | 多实例下 job 重复消费（未用 SKIP LOCKED）                            | 水平扩容不安全       | 扩容前必须先修                           |

## 10. 优先级汇总表

> 口径与 `docs/archive/2026-10/feature-gap.md` 对齐：**P0** = M0 内必须完成（否则平台不可信）；**P1** = M1/M2；**P2** = M3 之后或条件触发。规模沿用 S（≤2 天）/ M（3–5 天）/ L（1–2 周+）。

| 优先级 | 事项                                             | 建议 | 依赖            | 规模 | 建议里程碑 |
| ------ | ------------------------------------------------ | ---- | --------------- | ---- | ---------- |
| P0     | usage 生产者闭环（gateway usage → usage_events） | 采纳 | —               | M    | M0         |
| P0     | quota 准入判断（建 run / 调模型前）              | 采纳 | usage           | S    | M0         |
| P0     | token 计量与上下文预算                           | 采纳 | usage           | S-M  | M0         |
| P0     | 审批策略引擎（替换布尔确认）                     | 采纳 | hooks 载体      | M    | M0-M1      |
| P0     | traceId + Sentry                                 | 采纳 | —               | S    | M0         |
| P0     | 多实例 job 领取（SKIP LOCKED）                   | 采纳 | PG              | S    | M0         |
| P1     | LLM 重试 / fallback / 429 退避                   | 采纳 | —               | S    | M1         |
| P1     | 断点续跑（非 PG 环境）                           | 采纳 | PG checkpointer | M    | M1         |
| P1     | hooks（pre/post tool 等）                        | 采纳 | 事件流          | M    | M1         |
| P1     | Skills DB 化 + manifest 生效                     | 采纳 | hooks/策略      | M    | M1         |
| P1     | 子 run（parent_run_id）                          | 采纳 | 状态机          | M    | M1-M2      |
| P1     | 压缩投影（context.compacted.v1）                 | 采纳 | token 计量      | M    | M1-M2      |
| P1     | 黄金 run 回归                                    | 采纳 | traceId/事件流  | M    | M1         |
| P1     | Redis 限流                                       | 采纳 | Redis           | M    | M1         |
| P1     | 沙箱 provider（E2B / Fly）spike                  | 采纳 | M1-00 决策      | S+M  | M1         |
| P2     | Temporal / Inngest                               | 暂缓 | —               | L    | M3+        |
| P2     | mem0 / Zep 记忆服务                              | 暂缓 | —               | M    | M2+        |
| P2     | 浏览器 / computer use                            | 暂缓 | 沙箱与 egress   | M    | M2+        |
| P2     | Skill / 插件市场                                 | 暂缓 | Skills DB 化    | M    | M3         |
| P2     | Stripe 支付、企业 SSO/RBAC/审计导出              | 暂缓 | usage/quota     | L    | M3+        |
