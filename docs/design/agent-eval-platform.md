# monorepo Agent 评测平台设计（evalkit）

状态：**MVP 已落地（2026-11-24）**，当前阶段实现见 §10；后续路线见 §11。
本文是全仓库统一评测平台的单一真源；agent-orchestrator 单服务的 eval harness 契约仍见
[eval-and-cost-dashboard.md](./eval-and-cost-dashboard.md)，两者是「通用平台 + 服务适配器」的关系。

## 1. 背景与问题

仓库已有两套彼此独立的评测资产：

- `services/agent-orchestrator/agent_orchestrator/evals/`：进程内 TestClient + 脚本化 FakeLLM，
  3 个内置套件（chat-direct / task-approval / tool-policy），JSON 报告 + 基线漂移，已经进 CI。
- `services/agent-orchestrator/agent_orchestrator/evals/retrieval_quality.py`：固定 golden 语料 +
  9 条查询，对比 legacy / bm25 / bm25_mmr 三代排序口径，输出 recall@k、MRR、冗余度。

它们证明了「零网络、确定性、可进 CI」这条路可行，但存在四个结构性限制：

1. **用例写死在 Python 里**：新增用例要改代码、走服务发布，产品和评测同学无法独立维护数据集。
2. **断言与执行耦合**：AO 的 `expect` 只能表达该服务预置的少量键；检索指标、记忆指标各有各的输出格式，
   跨域无法用同一套门禁比较。
3. **没有统一的基线/报告协议**：AO 用 `evals/baseline.json`，检索脚本没有基线，CI 只覆盖前者。
4. **无法横向扩展**：要接知识库、记忆、连接器、模型网关，只能继续往 AO 里塞特判。

目标是把上述能力泛化成 monorepo 级评测平台：

- **数据集即数据**：用例放在 `evals/datasets/`，声明式 JSONL，不改代码即可增删。
- **adapter / grader 分离**：adapter 只负责「怎么跑被测目标」，grader 只负责「怎么判分」。
- **统一 Observation**：任何被测目标都归一成输出、事件、工具调用、结构化字段。
- **基线门禁**：行为、分数、token、指标、事件集、工具集发生非预期变化时 CI 失败。
- **多格式报告**：JSON / JUnit / Markdown / HTML，既能给机器门禁，也能给人看。
- **零新运行时依赖**：核心只用 Python 标准库，避免评测平台反过来成为供应链和升级负担。

**当前非目标**：真实模型在线质量评测、LLM-as-judge 生产化、自动生成测试集、前端评测面板、
成本预测。它们属于 §11 的后续阶段，不在 MVP 里假装完成。

## 2. 主流开源框架调研与取舍

| 框架                  | 核心抽象                                          | 值得借鉴                                 | 不作为运行时依赖的原因                                      |
| --------------------- | ------------------------------------------------- | ---------------------------------------- | ----------------------------------------------------------- |
| promptfoo             | 声明式 YAML 用例 + provider + assert，CLI/CI 友好 | 数据集与配置分离、矩阵对比、CI 退出码    | Node 生态与仓库 Python 服务链割裂；引入后要维护第二套运行时 |
| DeepEval              | pytest 风格用例 + LLM 指标                        | 测试即代码、指标可组合、pytest 集成      | LLM judge 依赖模型与密钥，MVP 要求零网络；指标库较重        |
| Inspect AI            | task / solver / scorer，日志与沙箱                | 执行与评分解耦、可复现日志、数据集版本   | 依赖面大，接入现有进程内 harness 需要重写适配层             |
| Ragas                 | RAG 检索/生成指标                                 | recall、MRR、上下文相关性等指标口径      | 面向 RAG 在线链路，不能直接覆盖编排/工具/记忆               |
| Langfuse              | datasets / experiments / traces                   | 实验是一等概念、trace 关联、生产反馈闭环 | 需要服务端与数据库，适合后续观测层而非 CI 门禁核心          |
| lm-evaluation-harness | 学术基准 + 统一 task 注册                         | 任务注册表、few-shot 配置、可复现        | 面向静态 NLP 基准，不适合有状态 Agent 轨迹                  |
| MLflow                | 实验跟踪与模型注册                                | run 元数据、对比视图、制品管理           | 平台过重，CI 里只需要报告与基线                             |

**决策**：不把上述框架作为运行时依赖，而是抽取它们共同验证过的模式，做一个薄核心 `evalkit`：

- 来自 promptfoo：数据集/配置声明化、CLI 门禁、矩阵化 suite。
- 来自 DeepEval：case 即测试、grader 可组合、失败原因可读。
- 来自 Inspect AI：adapter（solver）与 grader（scorer）分离、Observation 归一。
- 来自 Ragas：检索指标口径（recall@k、MRR、冗余度）。
- 来自 Langfuse：baseline/experiment 的思路，但先用文件而不是服务端。
- 来自 lm-eval / MLflow：任务注册与 run 报告，但保持文件化、可 diff。

这样做的代价是要自己维护 schema 和 grader；收益是零依赖、完全离线、可直接被 Nx 编排，
并且未来要把结果投递到 Langfuse/MLflow 时，只需要加一个 exporter，不需要改用例与评分。

## 3. 分层评测体系

评测不是一件事，按「反馈速度 × 真实度」分四层，每层有不同门禁：

| 层级              | 评什么                                         | 运行方式                     | 频率/门禁                              | 当前状态       |
| ----------------- | ---------------------------------------------- | ---------------------------- | -------------------------------------- | -------------- |
| L0 单元/契约      | 函数、API 契约、错误码                         | pytest / vitest              | 每次 PR                                | 已有，全仓测试 |
| L1 确定性行为     | 对话分流、run 状态机、审批、工具策略、事件序列 | 进程内 ASGI + 脚本化模型     | 每次 PR（evalkit agent-core）          | **已落地**     |
| L2 组件质量       | 检索排序、记忆抽取、分块、上下文压缩           | 固定语料 + 纯函数/进程内     | 每次 PR（retrieval-quality）+ 后续扩展 | **部分落地**   |
| L3 真实模型端到端 | 真实模型下的任务成功率、回复质量、成本         | live adapter + 录制回放/预算 | nightly / 发布前                       | 后续           |
| L4 在线评测       | 生产采样、用户反馈、漂移监控                   | trace + 抽样 + 人工复核      | 持续                                   | 后续           |

评测维度与当前覆盖：

| 维度     | 关键问题                                           | 当前手段                               | 后续                             |
| -------- | -------------------------------------------------- | -------------------------------------- | -------------------------------- |
| 对话     | 该直答的是否直答、该建 run 的是否建 run            | agent-core chat + conversation-quality | 工具型多轮、长上下文、角色一致性 |
| 任务编排 | 计划、审批、确认、完成、交付物、取消/fork/崩溃续跑 | agent-core task + run-resilience       | checkpoint 分叉、上下文压缩      |
| 工具调用 | 调了什么、参数对不对、该不该确认                   | tool-arguments + 策略用例              | MCP 工具、更多参数 schema        |
| 检索     | 相关条目是否召回、排序是否合理、是否冗余           | recall@k / MRR / redundancy            | 嵌入检索、混合检索、分块         |
| 记忆     | 该记的记住、不该记的不记、重复率                   | memory-quality（已接入 evalkit）       | 抽取口径扩充、上下文压缩保留率   |
| 效率     | token、时延、事件数                                | tokens / duration / min/max events     | 成本、缓存命中                   |
| 安全     | 危险操作是否被拦、是否泄漏                         | 策略用例 + 事件排除                    | red-team 数据集                  |

原则：**能在 L1/L2 用确定性手段覆盖的，不轻易上 L3**；L3 负责发现「假环境掩盖的真实问题」，
但必须可重复、有预算上限，不能成为不稳定的 PR 门禁。

## 4. 总体架构

```text
evals/
  evalkit.toml                  # 平台配置：套件 → 数据集 / adapter / 基线 / 阈值
  datasets/<name>/
    manifest.json               # 数据集元数据与默认 adapter
    cases.jsonl                 # 一行一个用例
  baselines/<name>.json         # 人工确认后更新的行为基线
        │
        ▼
packages/evalkit（薄核心，仅标准库）
  config → schema → runner → adapter → Observation → graders → SuiteRun
                                      │
                                      ├→ report（JSON / JUnit / Markdown / HTML）
                                      └→ baseline（compare → drift）
        │
        ▼
服务侧 adapter（不进核心包）
  agent_orchestrator.evals.platform_adapter:AgentOrchestratorAdapter
  agent_orchestrator.evals.platform_adapter:RetrievalQualityAdapter
```

依赖方向是单向的：`evalkit` 不知道任何具体服务；服务通过 `module:Class` 字符串被动态加载。
因此新增一个被测目标不需要改核心包，也不会让核心包反向依赖 FastAPI/SQLAlchemy。

### 4.1 核心模块

| 模块                  | 职责                                                                                    |
| --------------------- | --------------------------------------------------------------------------------------- |
| `evalkit/schema.py`   | DatasetManifest / Case / Dataset 校验；未知字段、非法 id、grader 结构错误都在加载期失败 |
| `evalkit/graders.py`  | Observation 定义；13 种 grader；权重、required、嵌套组合；中文失败原因                  |
| `evalkit/adapters.py` | Adapter 协议、注册表、`module:Class` 动态加载、构造参数兼容                             |
| `evalkit/config.py`   | 解析 `evals/evalkit.toml`；相对路径归一化；套件选择                                     |
| `evalkit/runner.py`   | 执行套件、过滤 tags/case、单用例异常隔离、adapter 生命周期                              |
| `evalkit/baseline.py` | 生成/加载/比较基线；漂移分类                                                            |
| `evalkit/report.py`   | JSON/JUnit/Markdown/HTML 与控制台摘要                                                   |
| `evalkit/cli.py`      | run/list/validate/version；退出码契约                                                   |

### 4.2 数据流

1. CLI 读 `evals/evalkit.toml`，确定要跑的套件。
2. runner 加载数据集：`manifest.json` 给默认 adapter，`cases.jsonl` 逐行解析。
3. 按 suite 配置或 manifest 默认值加载 adapter（服务侧类）。
4. 对每个 case 调用 `adapter.run(case)`，得到 `Observation`。
5. graders 按 case 的规则评分，得到 `CaseRun`；异常被转成执行错误，不中断整套。
6. 汇总为 `SuiteRun`，生成报告；有基线时比较并输出 drift。
7. 按失败、阈值、token 上限、漂移决定退出码。

## 5. 数据集规范

### 5.1 目录与 manifest

每个数据集是一个目录：

```text
evals/datasets/agent-core/
  manifest.json
  cases.jsonl
```

`manifest.json`：

```json
{
  "name": "agent-core",
  "version": 1,
  "description": "Agent 编排核心行为：对话分流、审批、工具策略",
  "owner": "agent-platform",
  "adapter": "agent_orchestrator.evals.platform_adapter:AgentOrchestratorAdapter",
  "tags": ["agent", "orchestration", "offline"]
}
```

约定：

- `name` 必须匹配 `^[a-z0-9][a-z0-9._/-]*$`，且与目录名一致（由评审保证）。
- `version` 是整数；**改变用例语义或评分口径时递增**，基线文件记录该版本。
- `adapter` 可被 suite 配置覆盖；数据集自带默认值，便于单独运行。
- `owner` 是维护责任人或团队，新增数据集必须填。
- `tags` 是数据集级标签；case 级 tags 用于更细的过滤。

### 5.2 case 结构

`cases.jsonl` 一行一个 JSON 对象，不允许跨行：

```json
{
  "id": "task-approval/approve-then-complete",
  "group": "task-approval",
  "tags": ["task", "approval"],
  "input": {
    "kind": "task",
    "goal": "整理本周日程并发布周报文档",
    "llm_script": [
      "# 执行计划\n- [ ] 检索日程\n- [ ] 发布说明",
      {
        "tool_calls": [
          { "name": "external_publish", "arguments": { "title": "周报" } }
        ]
      },
      "已发布周报。",
      "# 交付物\n\n周报已发布。"
    ],
    "steps": ["create_run", "approve_plan", "confirm_all", "wait_terminal"]
  },
  "graders": [
    {
      "id": "status",
      "type": "equals",
      "field": "fields.run_status",
      "value": "completed"
    },
    { "id": "tool", "type": "tool_calls", "value": ["external_publish"] },
    {
      "id": "deliverables",
      "type": "equals",
      "field": "fields.deliverables",
      "value": 1
    },
    { "id": "tokens", "type": "number", "field": "fields.tokens", "max": 5000 }
  ]
}
```

字段：

| 字段      | 必填 | 说明                                                        |
| --------- | ---- | ----------------------------------------------------------- |
| `id`      | 是   | 全局唯一，建议 `<场景>/<行为>`；只允许小写字母数字与 `._/-` |
| `group`   | 否   | 默认 `default`；用于报告分组和 adapter 路由                 |
| `tags`    | 否   | 字符串数组；`--tag` 取交集过滤                              |
| `input`   | 是   | 任意 JSON 对象；**平台不解释**，由 adapter 定义契约         |
| `graders` | 是   | 非空数组；见 §6                                             |
| `notes`   | 否   | 给人看的背景、已知限制、来源                                |

**input 是 adapter 的私有契约**，这是平台能同时接编排、检索、记忆的关键：
平台只要求它是对象，不要求固定字段。代价是每个 adapter 必须自己校验并给出清晰错误。

编排 adapter 的 input 额外支持 `messages`：当 `steps` 里有多个 `send_chat` 时，
逐轮取对应正文；条数必须与 `send_chat` 数一致，缺省时每轮沿用 `goal`。
这让多轮/澄清用例可以复用同一条步骤协议，而不引入新的步骤名。

韧性用例在既有步骤协议上追加五个动作：

- `cancel_run`：取消当前 run；之后 `wait_terminal` 应看到 `cancelled`，并断言
  `run.cancelled.v1` 存在、`run.completed.v1`/`run.failed.v1` 与工具调用不存在。
- `fork_run`：从当前 run 分叉出 pending 子 run，并把后续步骤切到子 run；
  父 run 保持不变。子 run 继承计划时，其 `approve_plan` 不再消耗脚本化模型回复。
- `plan_run`：只规划、不审批，把 run 推进到计划闸；用于验证计划闸 checkpoint。
- `crash_run`：把 run 状态改回 `running`，模拟进程崩溃后的现场；不经过 API。
- `recover_run`：直接调用服务层 `recover_run`，再读 `run.recovered.v1`；
  用于验证从 checkpoint 续跑、无 checkpoint 回落新 attempt、终态图直接收尾。

### 5.3 数据治理

- **确定性**：L1/L2 用例不得依赖当前时间、随机数、网络、真实模型；时间相关逻辑由 adapter 注入固定时钟。
- **最小化**：只放评测必需的输入与期望，不复制生产数据；禁止真实密钥、用户隐私、内部地址。
- **可归因**：失败信息要能定位到 case id 和 grader id；case 的 `notes` 写清来源与假设。
- **可演进**：新增用例优先加在已有数据集；语义变化改 `version` 并重生成基线，PR 里单独说明。
- **覆盖分层**：每个数据集至少包含 1 条正向、1 条负向/边界；禁止只有 happy path。

## 6. 指标与 grader

### 6.1 Observation：统一观测

adapter 返回：

```python
Observation(
    output="最终回复文本",
    fields={"run_status": "completed", "tokens": 1046, "metrics": {...}},
    events=("run.created.v1", "run.completed.v1"),
    tool_calls=("external_publish",),
    error=None,
)
```

- `output`：文本结果；对话类断言默认看它。
- `events` / `tool_calls`：轨迹；用于顺序无关的集合断言与漂移检测。
- `fields`：结构化指标；`tokens` 与 `metrics` 有特殊约定，分别进入汇总和基线。
- `error`：adapter 认为用例执行失败时置值；grader 会直接判 0 分并保留原因。

字段路径支持 `output`、`events`、`tool_calls`、`fields.<key>` 和点路径（如 `fields.metrics.mrr`）。
对话 adapter 还暴露 `fields.replies`（逐轮回复）、`fields.turn_count`、`fields.reply_<n>`
与 `fields.prompt_<n>`（第 n 次模型调用的入参 JSON），用于验证历史是否真的注入。
韧性 adapter 额外暴露 `fields.run_attempts`、`fields.recovered`（完整恢复 payload）
与 `fields.recovered_from_status/next/fallback/resumed/checkpoint_id`。

编排 adapter 把每次工具调用压成结构化记录，按调用顺序暴露：

- `fields.tool_call_names`：名称列表（保序），用 `equals` 精确断言调用顺序。
- `fields.tool_call_arguments`：参数列表（保序），用 `equals` 精确断言参数对象。
- `fields.tool_call_details`：`name`、`arguments`、`ok`、`risk`、`decision`、
  `error_code`、`duration_ms`；用 `json_subset` 断言其中关心的键。
- `fields.tool_call_count`：调用次数，用 `number equals` 断言。

`decision` 区分 `auto`（策略/只读自动放行）、`approved`（人工确认）、
`hook_denied`（hook 拒绝）等；参数是事件里的 JSON 文本，解析失败时保留原字符串，
避免采集层把脏数据吞成“看起来正确”。

fork 用例还暴露血缘快照，便于断言“子 run 成功且父 run 不被改写”：

- `fields.run_detail`：最终 run 的只读快照（`id`/`title`/`status`/`goal`/
  `parent_run_id`/`parent_kind`/`priority`/`timeout_seconds`）。
- `fields.parent_run_detail`：fork 前父 run 的快照；非 fork 用例为空对象。
- `fields.parent_run_id`、`fields.parent_status`、`fields.parent_kind`、
  `fields.run_title`：上述两个快照的常用平铺视图，直接用于 `equals`/`contains`。

注意：事件、工具调用与交付物只采集**当前（fork 后为子）run**；父 run 的状态
通过 `parent_status` 断言，避免把两条 run 的事件混在一起。

### 6.2 grader 类型

| 类型                        | 语义                                          | 关键参数         |
| --------------------------- | --------------------------------------------- | ---------------- |
| `equals`                    | 严格相等（数字跨 int/float，bool 不与数字混） | `field`, `value` |
| `contains` / `not_contains` | 字符串包含 / 不包含                           | `value`          |
| `regex`                     | 正则搜索                                      | `pattern`        |
| `includes` / `excludes`     | 集合包含 / 排除                               | `value`（数组）  |
| `count`                     | 集合/字符串长度范围                           | `min`, `max`     |
| `number`                    | 数值范围                                      | `min`, `max`     |
| `json_subset`               | JSON 子集匹配                                 | `value`          |
| `tool_calls`                | 工具调用集合包含                              | `value`          |
| `all` / `any` / `not`       | 组合子 grader                                 | `graders`        |

通用参数：`id`（套件内唯一）、`weight`（默认 1.0）、`required`（默认 true）、`message`（可选覆盖失败文案）。

评分规则：

- 每个 grader 通过得 `weight`，失败得 0；case score = 通过权重 / 总权重，保留 4 位。
- case 的 `ok` 要求**所有 required grader 通过**；非 required 只影响分数。
- adapter 抛异常或返回 error 时，score=0、ok=false，失败原因以「执行错误:」开头。
- 失败信息包含字段、期望、实际，便于直接贴进 PR；过长实际值会截断。

### 6.3 指标口径

当前已落地：

| 指标          | 口径                         | 用途                                  |
| ------------- | ---------------------------- | ------------------------------------- |
| `tokens`      | adapter 上报的总 token       | 成本回归（+20% 漂移）                 |
| `duration_ms` | 单用例耗时                   | 报告参考；**不参与漂移**（CI 噪声大） |
| `recall_at_k` | 前 k 条命中相关条目的比例    | 检索召回                              |
| `mrr`         | 第一个相关条目的倒数排名     | 检索排序                              |
| `redundancy`  | 第 1 名与第 2 名的词元相似度 | 前两条多样性                          |

后续指标（按优先级）：

- 任务成功率、计划合法率、工具参数正确率、确认闸命中率。
- 记忆 precision / recall / duplicate_rate / acceptance_rate（现有 `memory_quality.py` 已有实现）。
- 上下文压缩后的信息保留率、引用命中率。
- 成本（按模型价目表换算）、P50/P95 时延、缓存命中率。
- 安全性：危险工具拦截率、越权访问拒绝率、敏感信息泄漏率。

## 7. 执行架构与零网络

### 7.1 Adapter 协议

```python
class Adapter(Protocol):
    name: str
    def run(self, case: Case) -> Observation: ...
    def close(self) -> None: ...
```

- 通过 `module:Class` 动态导入，也支持注册名。
- 构造兼容 `__init__(options=...)`、`__init__(options)`、`__init__()` 三种形态。
- 平台负责在 `finally` 里调用 `close()`；adapter 自己管理进程内资源。
- adapter 不应吞掉 schema 错误：非法输入应抛出带 case id 的 `ValueError`，让评测显式失败。

### 7.2 现有 adapter

| adapter                    | 驱动方式                                                                 | 暴露字段                                                                                                                        |
| -------------------------- | ------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------- |
| `AgentOrchestratorAdapter` | 复用 AO 的 `run_case`：临时 sqlite、TestClient、脚本化 LLM、NetworkGuard | run_status、tokens、deliverables、duration_ms、events、tool_calls、tool_call_names/arguments/details/count、replies、turn_count |
| `RetrievalQualityAdapter`  | 复用 `run_variant`：固定 10 条语料、单查询、指定排序口径                 | ranked、metrics.recall_at_k、metrics.mrr、metrics.redundancy、variant                                                           |

两者都在服务包内，核心包不依赖服务。新增服务时按同样方式写一个 adapter 即可。

### 7.3 零网络与确定性

L1/L2 的硬约束：

- AO adapter 走进程内 ASGI，不监听端口、不发真实 HTTP。
- `NetworkGuard` 在用例执行期间替换 `socket.socket`，任何 `AF_INET/AF_INET6 connect` 都抛错并记为违规。
- FakeLLM 按脚本顺序返回内容，不调用模型网关；检索 adapter 只读固定语料。
- 测试/评测 settings 显式钉死所有外部开关（LLM、工具、知识、MCP、outbox 等），
  且使用临时 sqlite，不读写本地开发库。
- 单用例有等待上限（AO 侧 10s），避免 CI 挂死。

## 8. 基线与门禁

### 8.1 基线内容

`evals/baselines/<suite>.json` 每个 case 记录：

```json
{
  "ok": true,
  "score": 1.0,
  "tokens": 1046,
  "metrics": { "recall_at_k": 1.0, "mrr": 1.0, "redundancy": 0.0668 },
  "events": ["run.completed.v1", "run.created.v1"],
  "tool_calls": ["external_publish"]
}
```

events/tool_calls 排序去重，避免顺序噪声；metrics 只记录数值。

### 8.2 漂移判定

| 类型          | 判定                                      | 说明                                       |
| ------------- | ----------------------------------------- | ------------------------------------------ |
| 套件/用例增删 | 基线有而当前没有，或反之                  | 防止误删覆盖                               |
| 失败          | `ok` 从 true 变 false                     | 最高优先级                                 |
| 分数下降      | 当前 score + 容差 < 基线                  | 只报下降，不报上升                         |
| token 增长    | 当前 > 基线 × 1.2                         | 成本回归；严格大于                         |
| 指标变化      | 任一 metric 绝对值变化 > 1e-9，或指标消失 | 方向由 grader 约束（如 redundancy 用 max） |
| 事件集变化    | 集合增删                                  | 轨迹契约                                   |
| 工具集变化    | 集合增删                                  | 工具面契约                                 |

**不比较 duration**：CI 机器负载波动大，比较它只会制造噪声；时延作为报告信息保留，
需要门禁时用独立的 P95 预算，而不是逐用例基线。

### 8.3 更新流程

1. 本地跑 `bash scripts/eval/platform.sh run`，确认失败/漂移是预期的。
2. 人工审查 diff：为什么行为变了、是否是改进、是否要补用例。
3. 执行 `bash scripts/eval/platform.sh run --update-baseline`。
4. 基线文件随代码一起提交，PR 描述里写明原因。
5. 禁止在 CI 中自动更新基线；有失败时 CLI 会拒绝更新。

### 8.4 退出码

| 码  | 含义                                          | CI 处理            |
| --- | --------------------------------------------- | ------------------ |
| 0   | 全通过、阈值达标、无漂移                      | 通过               |
| 1   | 有用例失败 / fail_under 未达标 / token 超上限 | 失败               |
| 2   | 仅与基线漂移                                  | 失败，要求人工确认 |
| 3   | 配置、数据集、adapter、参数错误               | 失败，修配置       |

## 9. 报告与看板

每次运行默认写 `reports/eval/<UTC 时间戳>` 下的四份文件：

- `.json`：机器可读的完整报告（totals、suites、cases、graders、events、tool_calls）。
- `.junit.xml`：CI 测试报告，失败用例可在 GitHub Actions 里直接看到。
- `.md`：PR 评论/归档友好的汇总与失败明细。
- `.html`：自包含单文件（内联 CSS），无外部依赖，可直接作为构建产物打开。

控制台同时打印套件表（用例数、通过率、得分、tokens、耗时）。HTML 报告 + JUnit 是
一次运行的归档形态；常驻工作台见 §9.1，趋势对比与门禁治理留给 §11 的 P1/P2。

### 9.1 质量工作台（M1：eval-service）

不做评测报告 iframe，而是把运行快照变成可下钻的质量工作台：

| 路由                 | 内容                                                                                              |
| -------------------- | ------------------------------------------------------------------------------------------------- |
| `/evals`             | KPI（通过率/失败/漂移/套件/tokens/耗时）、套件健康度、最近运行、需要关注的失败用例                |
| `/evals/runs/:runId` | 运行元数据、套件页签、状态筛选、用例表；`?case=&tab=` 打开右侧抽屉（结果/轨迹/输入输出/原始数据） |

- 数据层：`apps/web/src/lib/eval-api.ts` 优先读同源 `/api/eval/runs`
  （网关 → eval-service `:8009`），失败回退 M0 静态快照
  `apps/web/public/eval/index.json` 与 `runs/<id>.json`；页面、URL 过滤协议与组件不变。
- 写入：CI 用 `scripts/eval/upload.mjs` 读取 `reports/eval/` 最新 JSON，
  以 `X-Internal-Secret` 调 `POST /v1/runs`；同 `external_id` 幂等。
  服务端用上报携带的基线或已批准基线计算漂移，把 `drifts` / `drift_count` /
  `baseline_diff` 回填进报告快照，因此 `/evals` 的漂移数字不再是 0。
- 读取：鉴权开启时页面走账号 JWT（`get_viewer_account` 严格校验 Bearer）；
  本地/演示按仓库惯例 `AUTH_ENABLED=false` 时只读端点匿名可用（网关仍做会话鉴权），
  此前该组合下页面会因 401 静默回退静态快照，现由匿名只读修复。写入口只认内部密钥，
  基线批准仍要求账号。运行索引支持套件/结果/分支过滤，用例分页端点
  （`/v1/runs/{id}/suites/{suite}/cases`，默认 50、上限 500）已就绪，
  运行详情仍取完整报告，500+ 用例接入服务端分页留到 M2。
- 存储：`eval_runs`（报告快照 + totals/失败/数据集版本）、`eval_suite_runs`、
  `eval_case_runs`（逐用例结果/轨迹/评分器/基线差异）、`eval_baselines`；
  大字段先落 TEXT，对象存储与保留期策略后置。
- URL 是真源：`?suite=`、`?status=passed|failed`、`?case=`、`?tab=result|trajectory|io|raw`。
- 导航入口放在「管理」组、用量之前，第一版 `adminOnly=true`；页面本身不替代
  CI 门禁，失败/漂移仍以 `platform.sh` 退出码为准。
- M1 限制：没有跨运行对比 API/UI、数据集只读清单、门禁治理与对象存储；
  这些属于 M2。

## 10. 当前阶段实施（已完成）

### 10.1 代码与配置

| 位置                                                                       | 内容                                                                                                                     |
| -------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------ |
| `packages/evalkit/evalkit/`                                                | schema / graders / adapters / config / runner / baseline / report / cli                                                  |
| `packages/evalkit/tests/`                                                  | 130 条单元测试，覆盖 schema、grader、adapter 加载、runner、基线、报告、CLI                                               |
| `evals/evalkit.toml`                                                       | `agent-core`、`retrieval-quality`、`conversation-quality`、`tool-arguments`、`memory-quality`、`run-resilience` 六个套件 |
| `evals/datasets/agent-core/`                                               | 3 条用例：对话直答、任务审批、工具策略                                                                                   |
| `evals/datasets/retrieval-quality/`                                        | 9 条查询，覆盖短词、中英混合、近重复、长词                                                                               |
| `evals/datasets/conversation-quality/`                                     | 3 条对话：多轮上下文、澄清追问、拒答不建 run                                                                             |
| `evals/datasets/tool-arguments/`                                           | 2 条工具参数：人工确认链路的参数/风险/决策、自动策略下的双工具顺序与参数                                                 |
| `evals/datasets/memory-quality/`                                           | 6 条记忆 golden：偏好/事实、重复、低置信、敏感、闲聊                                                                     |
| `evals/datasets/run-resilience/`                                           | 6 条韧性：取消 pending、工具失败后恢复、fork 独立完成、崩溃续跑三入口（回落/计划闸/终态）                                |
| `evals/baselines/`                                                         | 六个套件的基线                                                                                                           |
| `services/agent-orchestrator/agent_orchestrator/evals/platform_adapter.py` | 三个服务侧 adapter（编排、检索、记忆；对话套件复用编排 adapter）                                                         |
| `scripts/eval/platform.sh`                                                 | 仓库根入口，复用本地 uv 缓存                                                                                             |
| `services/eval-service/`                                                   | 评测服务（:8009）：四张表、上报/索引/详情/用例分页/基线批准端点、alembic 迁移                                            |
| `scripts/eval/upload.mjs`                                                  | 把最新报告与基线以内部密钥上报 eval-service；同 `external_id` 幂等                                                       |
| `packages/evalkit/project.json`                                            | Nx target：lint/format/test/build/sync/eval/eval-baseline                                                                |
| `scripts/eval/publish-web.mjs`                                             | 把 JSON 报告发布成 `public/eval` 索引与运行快照（裁剪大字段）                                                            |
| `apps/web/public/eval/`                                                    | M0 演示快照与回退：`index.json` + `runs/<id>.json` + README；eval-service 不可用时页面仍可看                             |
| `apps/web/src/pages/evals.tsx`、`eval-run.tsx`                             | 质量工作台总览与运行详情                                                                                                 |
| `apps/web/src/features/evals/`                                             | KPI、套件健康度、运行历史、需要关注、用例表、用例抽屉、评分器、工具轨迹                                                  |
| `apps/web/src/lib/eval-api.ts`                                             | 数据层：优先 eval-service，失败回退静态快照                                                                              |

### 10.2 门禁接入

- `npx nx run evalkit:eval` 跑全量套件并与基线比对。
- `npx nx run evalkit:eval-baseline` 人工更新基线。
- CI `e2e` job 在 golden-run、AO eval 之后追加 `bash scripts/eval/platform.sh run`，
  再用 sqlite 启动 eval-service 并调 `node scripts/eval/upload.mjs` 验证上报链路。
- `evalkit` 与 `eval-service` 进入 uv workspace、Nx 项目、release 排除列表、版本门禁
  与 Python lint/test 矩阵（13 个项目）；`eval-service` 另有 alembic 迁移检查 job。
- 覆盖率阈值：evalkit 85（实测 96.03%）、eval-service 80（实测 94.06%）。

### 10.3 实测结果

- `evalkit`：130 passed，覆盖率 96.03%，ruff 全通过。
- AO 全量：1399 passed，覆盖率 88.32%（阈值 82%），含 19 条平台 adapter 测试
  与 3 条工具调用采集测试。
- eval-service：21 passed，覆盖率 94.06%（阈值 80%）；四张表、上报幂等、漂移回填、
  基线批准、索引/分页过滤、鉴权关闭时匿名只读均有测试。api-gateway 因新增 eval 前缀
  56 passed、87.50%。
- 平台运行：6 个套件、29 条用例全部通过；agent-core 3 条 / 1046 tokens，
  retrieval-quality 9 条，conversation-quality 3 条 / 368 tokens，tool-arguments
  2 条 / 1071 tokens，memory-quality 6 条，run-resilience 6 条 / 2383 tokens；
  整体约 4s，与基线零漂移。
- conversation-quality 固定了三类对话契约：第二轮 prompt 必须带上一轮用户与助手内容
  （历史注入）、澄清追问后能承接补充信息、拒答只留对话不建 run（events/tool_calls 为空）。
- tool-arguments 固定了参数级契约：审批链路下 `decision=approved`、自动策略下
  `decision=auto`；双工具用例按 `search_knowledge → external_publish` 顺序执行，
  参数对象与风险级别（read/danger）逐一断言，参数被改写或丢失会直接失败。
- memory-quality 固定了当前行为：高置信偏好/事实落库、重复候选解析期折叠、
  低置信进复核、敏感凭据拒绝且同批正常事实保留；闲聊 note 目前仍会写入，
  用 `known-gap` 标签记录现状，待 P2 收紧为不进入系统。
- run-resilience 固定了六条韧性契约：pending 取消后只留 `run.cancelled.v1` 且零工具；
  未知工具返回 `error_code=tool.error` 后 run 不终止，下一轮可改用正确工具完成；
  fork 出的子 run 继承计划、独立完成，父 run 保持 `completed` 且 `parent_kind=fork`；
  崩溃后无 checkpoint 回落到新 attempt（attempts=2、fallback=plan）；
  计划闸 checkpoint 续跑交回人工审批（next=["approval_gate"]）；
  终态图直接收尾、不新增模型调用（status 保持 completed）。
- 版本门禁：21 处版本字段（JS 7 + 根 pyproject + Python 13）。
- Web 质量工作台：`@beauty/web` 832 条测试、115 个测试文件全部通过；`typecheck`、
  `lint`、`--run --coverage` 与生产构建通过。页面测试覆盖总览 KPI/套件/失败入口、
  运行详情 URL 过滤、用例抽屉评分器与工具轨迹、深链打开与 ESC 关闭。
  M1 数据面切到 eval-service（失败回退静态快照），页面协议与组件未变。

### 10.4 验收命令

```sh
bash scripts/eval/platform.sh validate          # 6 个套件、29 条用例
bash scripts/eval/platform.sh list --cases      # 列出用例
bash scripts/eval/platform.sh run              # 全通过 + 无漂移，退出 0
node scripts/eval/publish-web.mjs --limit 2     # 发布静态快照到 public/eval
npx nx typecheck @beauty/web
npx nx test @beauty/web -- --run
npx nx test @beauty/web -- --run --coverage
npx nx build @beauty/web
npx nx lint evalkit
npx nx test evalkit
npx nx lint eval-service
npx nx test eval-service
node scripts/eval/upload.mjs --optional  # 服务可用则上报，不可用则跳过
bash scripts/check-versions.sh
bash scripts/check-workspace.sh
bash scripts/check-docs.sh
```

## 11. 后续路线

### P1（近期，1–2 个迭代）：把「平台」变成「体系」

1. **数据集扩充**：记忆质量 6 条 golden（P1.1，已完成）；对话多轮/澄清/拒答
   3 条（P1.2，已完成）；工具参数级正确性 2 条（P1.3，已完成）；任务取消、
   工具失败恢复、fork 3 条（P1.4，已完成）；崩溃续跑 3 条（P1.5，已完成）；
   继续补 checkpoint 分叉、上下文压缩保留率、连接器 OAuth 生命周期。
2. **live adapter + 录制回放**：新增真实模型 adapter，但默认用录制 cassette 回放；
   live 模式单独开关、预算上限、nightly 运行，不阻塞 PR。
3. **LLM-as-judge（可选 grader）**：pinned 模型、rubric、温度 0、结果缓存、人工校准集；
   只作为补充 grader，不与确定性 grader 混为同一分数。
4. **成本与时延**：按 `llm-gateway` 价目表把 tokens 换算成金额；报告增加 P50/P95；
   设置套件级预算门禁。
5. **趋势报告**：保留最近 N 次 JSON，生成 Markdown 趋势；PR 评论只贴摘要与失败用例。
6. **质量工作台**：M0 静态快照与页面已完成（`/evals` + `/evals/runs/:runId` + 用例抽屉）；
   M1 eval-service + Postgres + CI 上报已完成（见 §9.1）；M2 做跨运行对比、数据集/门禁
   治理、服务端分页接入、对象存储与保留期。

### P2（中期）：矩阵与生产反馈

1. **实验矩阵**：同一数据集跑多模型/多提示/多检索口径，输出对比表；
   对应 promptfoo 的 matrix、Langfuse 的 experiment。
2. **统计显著性**：重复 N 次，报告 pass@k、均值/方差、置信区间；区分真回归与抖动。
3. **Langfuse 导出**：把 SuiteRun/CaseRun 作为 dataset run 写入，复用 trace 视图；
   评测结果与生产 trace 可关联。
4. **数据闭环**：从生产失败 trace 抽样生成候选 case，经人工审核后进入数据集；
   禁止自动把线上数据直接变成门禁。
5. **覆盖度报告**：按维度统计用例数、最近失败、owner、基线年龄，防止“绿但没测”。

### P3（长期）：在线评测与安全

1. 生产采样评测（L4）：按比例抽样真实会话，离线重放/评分，监控漂移。
2. 人工复核队列：低分/高风险样本进入标注，形成 golden 集。
3. red-team 数据集：提示注入、越权工具、数据外泄、资源耗尽。
4. 多代理/长任务评测：子代理协作、长上下文、跨会话记忆。
5. 评测平台自身可观测：运行时长、成本、失败率、基线更新审计。

## 12. 风险与对策

| 风险             | 表现                 | 对策                                                              |
| ---------------- | -------------------- | ----------------------------------------------------------------- |
| 假环境偏离生产   | Fake 通过但线上失败  | 契约测试 + 定期 live/nightly + 录制回放                           |
| 基线腐化         | 长期不更新或随意更新 | PR 必须说明；基线年龄纳入覆盖度报告；语义变更递增 dataset version |
| 过拟合用例       | 只优化固定 29 条     | 留出集、按维度扩充、生产采样候选                                  |
| grader 太严/太松 | 频繁误报或漏报       | 失败信息可读；review grader；关键指标用范围而非精确值             |
| LLM judge 偏差   | 偏爱长答案/特定风格  | pinned 模型、rubric、人工校准、与确定性 grader 分离               |
| CI 变慢/变贵     | 套件膨胀             | Nx affected、tags 分层、nightly 跑重套件、预算上限                |
| 数据泄漏         | 密钥/隐私进仓库      | 合成数据、提交前扫描、manifest owner 审核                         |
| 单文件膨胀       | 维护困难             | 仓库 500 行上限；核心模块按职责拆分；adapter 放服务侧             |

## 13. 设计取舍记录（ADR 摘要）

- **ADR-E1：自研薄核心而非引入 promptfoo/DeepEval**。原因：polyglot monorepo、零网络 CI、
  现有进程内 harness 可复用、避免第二套运行时；代价是 schema/grader 自维护。
- **ADR-E2：adapter 放服务侧，核心包不反向依赖**。原因：依赖方向清晰、服务可独立演进；
  代价是跨服务用例要各自写 adapter。
- **ADR-E3：用例用 JSONL 而非 YAML**。原因：一行一 case、diff 清晰、标准库可直接解析；
  代价是长文本转义不如 YAML 友好，靠生成脚本/格式化缓解。
- **ADR-E4：基线只比较行为与成本，不比较耗时**。原因：CI 耗时噪声大；
  耗时作为报告信息，需要时用独立 P95 预算。
- **ADR-E5：失败/漂移退出码分离（1 vs 2）**。原因：CI 能区分“真的坏了”和“行为变了待确认”，
  便于 PR 里给出不同提示。
- **ADR-E6：LLM judge 后置且可选**。原因：MVP 必须零网络、可复现；judge 的偏差与成本需要单独治理。

## 14. 附：新增一个评测套件

1. 在 `evals/datasets/<name>/` 写 `manifest.json` 与 `cases.jsonl`。
2. 在服务包内实现 adapter（`run` 返回 Observation，`close` 清理资源）。
3. 在 `evals/evalkit.toml` 加 `[[suite]]`，指定 dataset、adapter、baseline、fail_under。
4. `bash scripts/eval/platform.sh validate` 校验 schema。
5. `bash scripts/eval/platform.sh run --suite <name>` 首次运行。
6. 人工审查结果后 `--update-baseline` 生成基线。
7. 再跑一次确认零漂移，提交数据集、adapter、配置、基线四件套。

新增套件的验收标准：至少 1 条正向 + 1 条边界；失败原因能定位到 case/grader；
`run` 在无网络环境可重复通过；基线可解释；CI 时间增量可接受。
