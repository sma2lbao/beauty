# 上下文工程管线设计(P1-F)

> 状态:**已落地(2026-10-27)**。范围:`services/agent-orchestrator`。实现与本文一致,实施记录与差异见 §12。
> 关联:`docs/gap-2026-10/08-refresh-2026-10-27.md` 第 5 条与 P1-F 条目;`docs/design/memory.md`(短期压缩/长期记忆)。

## 1. 摘要

把「对话压缩 + 大工具结果 offload + SKILL.md 渐进加载 + AGENTS.md 注入」收敛成**一条可插拔管线**,而不是四个散落开关:

- **策略快照**:`ContextPolicy`(纯值对象)是开关/预算/顺序的唯一真源,起图时快照进事件 `run.context.assembled.v1`,同一 run 回放可复现。
- **有序管线**:`ContextPipeline` 按 `policy.section_order` 依次调用 `SectionProvider`(agents_md → skills → memory),产出 `ContextPlan`(sections + skipped + token 估算),渲染成一段系统提示块 `context_block` 进图状态。
- **压缩策略**:沿用既有 `services/compaction.py`,阈值/保留条数/摘要预算改由 `ContextPolicy.compaction()` 提供(policy 是 settings 的运行期投影,不是第二份配置)。
- **大结果 offload**:工具执行结果超过阈值时,orchestrator 把完整结果落沙箱工作区文件(`/workspace/.agent/offload/<run_id>/...`),消息流里只留「引用信封 + 预览」;模型需要细节时用 `read_offloaded` 工具或 `read` 工具分段读取。
- **SKILL.md 渐进加载**:技能正文不再全文常驻系统提示,只注入 `load_skill` 卡片(~100 token);`load_skill` 工具按 `offset/limit` 分页返回正文,并落 `run.skill.loaded.v1` 事件。
- **AGENTS.md 注入**:从**沙箱工作区**(/workspace 根 + 子目录)发现 AGENTS.md,按根→深的顺序注入;不做宿主文件系统读取(多租户无宿主真源)。

## 2. 背景与现状

| 能力           | 落地前                                                             | 位置                                                                                                            |
| -------------- | ------------------------------------------------------------------ | --------------------------------------------------------------------------------------------------------------- |
| 对话压缩       | 已有:按阈值折叠最老消息进摘要                                      | `services/compaction.py`、`core/config.py` 的 `context_*`                                                       |
| run 内工具输出 | 硬截断:超过 25K 直接报错,tool 消息原样回填                         | `services/tool-service/tool_service/constants.py`(`MAX_TOOL_CHARS=25_000`)、`graph/nodes.py` 的 `_tool_message` |
| SKILL.md       | 全文常驻(plan/execute/verify/deliver 都注入 `skill_system_prompt`) | `graph/plan_nodes.py` 的 `_skill_block`、`services/run_execution.py`                                            |
| AGENTS.md      | 无注入(此前明确决定见 `docs/design/memory.md`:多租户无文件真源)    | —                                                                                                               |

本期改变第二个决定:**工作区沙箱内仓库文件**就是文件真源(租户/对话由 scope_key 隔离),因此可以注入 AGENTS.md;宿主文件系统仍然不读。

## 3. 目标与非目标

### 3.1 目标

1. 四类上下文来源都由同一管线装配,顺序/开关/预算集中在一个策略对象里。
2. 大工具结果不再以截断(或 25K 报错)收场:完整内容可回读,消息流 token 明显下降。
3. SKILL.md 正文按需加载,加载行为可观测(事件 + 审计)。
4. AGENTS.md 从沙箱工作区发现并注入,注入内容有快照(路径/字符数/截断)。
5. 全部能力可关:任一开关关闭时行为与改造前一致。

### 3.2 非目标

- 不引入向量检索式的上下文选择(记忆召回仍走 `services/memory.py`)。
- 不改 tool-service 的 25K 上限(单批工具结果 >25K 仍由 tool-service 报错;offload 覆盖 8K–25K 区间)。后续可在 tool-service 侧抬阈值。
- 不动 checkpointer/事件存储结构;上下文快照只以事件 payload 形式记录。
- 不做跨 run 的上下文缓存。

## 4. 与主流方案对照

| 能力           | 主流做法                                                   | 本项目落地                                                                 |
| -------------- | ---------------------------------------------------------- | -------------------------------------------------------------------------- |
| 仓库规则注入   | Claude Code 读 CLAUDE.md / Codex 读 AGENTS.md,注入系统提示 | 沙箱 /workspace 发现 AGENTS.md,根→深拼接,总预算截断                        |
| 技能渐进加载   | SKILL.md frontmatter 常驻(名称+描述),正文按需读取          | `load_skill` 卡片 + 分页正文工具,事件可观测                                |
| 大结果 offload | 工具输出落文件,消息里放引用;模型按需回读                   | `/workspace/.agent/offload/<run>/<call>.txt` + 引用信封 + `read_offloaded` |
| 压缩           | 阈值触发摘要,保留最近 N 轮原文                             | 复用既有 `compaction`,阈值进 policy 可复现                                 |

## 5. 总体架构

```text
settings ──policy_from_settings──▶ ContextPolicy ──snapshot──▶ run.context.assembled.v1
                                        │
             ContextPipeline.build(ContextRequest)
                                        │  (按 policy.section_order)
        ┌───────────────┬───────────────┼────────────────┐
        ▼               ▼               ▼                ▼
 AgentsMdProvider  SkillCardProvider  MemoryProvider   (可扩展)
 (sandbox 工作区)   (run 快照正文卡)   (services 召回块)
        └───────────────┴───────────────┴────────────────┘
                                        │
                                  ContextPlan ──render()──▶ state["context_block"]
                                        │                        │
                                        ▼                        ▼
                               state["context_snapshot"]   plan/execute 系统提示

工具结果 ──▶ ToolOutputOffloader.maybe_offload ──▶ 引用信封(进 tool 消息)
                     │                                   │
                     ▼                                   ▼
        沙箱 /workspace/.agent/offload/...        read_offloaded 工具回读
```

要点:

- **节点不碰 IO**:AGENTS.md/offload 的 IO 都在 `context/` 或 services 层;节点只读 state 里渲染好的 `context_block`,或调用由装配方注入的 offload 端口。
- **依赖方向**:`graph` 不 import `services`;offload 端口以 Protocol 形式定义在 `graph/ports.py`,实现落在 `context/offload.py`(接收 `current_tool_context()` 拿租户/run/对话)。
- **沙箱复用**:orchestrator 与 tool-service 共用同一沙箱作用域规则(`conversation:<id>` > `run:<id>` > `tenant:<id>`),否则 AGENTS.md 与工具看到的工作区不是同一份。

## 6. 模块契约

### 6.1 `agent_orchestrator/context/policy.py`

```python
SECTION_AGENTS_MD = "agents_md"
SECTION_SKILLS = "skills"
SECTION_MEMORY = "memory"
DEFAULT_SECTION_ORDER = (SECTION_AGENTS_MD, SECTION_SKILLS, SECTION_MEMORY)

@dataclass(frozen=True, slots=True)
class CompactionPolicy:
    enabled: bool
    threshold_tokens: int
    keep_recent_messages: int
    summary_max_chars: int

@dataclass(frozen=True, slots=True)
class ContextPolicy:
    enabled: bool = True
    section_order: tuple[str, ...] = DEFAULT_SECTION_ORDER
    # 压缩(委托 services.compaction)
    compaction_enabled: bool = True
    compact_threshold_tokens: int = 6000
    keep_recent_messages: int = 8
    summary_max_chars: int = 2000
    # AGENTS.md
    agents_md_enabled: bool = True
    agents_md_names: tuple[str, ...] = ("AGENTS.md",)
    agents_md_max_chars: int = 4000       # 所有文件合计预算
    agents_md_file_max_chars: int = 2000  # 单文件上限
    agents_md_max_depth: int = 2          # /workspace 下相对深度
    agents_md_max_files: int = 8
    # 工具结果 offload
    offload_enabled: bool = True
    offload_threshold_chars: int = 8000
    offload_preview_chars: int = 1200
    offload_dir: str = ".agent/offload"
    offload_max_chars: int = 200_000
    # SKILL.md 渐进加载
    skill_loading_enabled: bool = True
    skill_card_max_chars: int = 400
    skill_body_page_chars: int = 4000

    def compaction(self) -> CompactionPolicy
    def normalized_order(self) -> tuple[str, ...]   # 去掉未知段并保持顺序
    def to_dict(self) -> dict[str, Any]             # 可 JSON 序列化(事件快照)
    def fingerprint(self) -> str                     # sha256[:16] of canonical json
    def with_overrides(self, **changes: Any) -> ContextPolicy

def policy_from_settings(settings: Settings) -> ContextPolicy
```

### 6.2 `agent_orchestrator/context/pipeline.py`

```python
@dataclass(frozen=True, slots=True)
class ContextSection:
    id: str
    title: str
    content: str
    source: str = ""
    truncated: bool = False
    @property
    def chars(self) -> int
    def render(self) -> str                          # "\n\n## <title>\n\n<content>"
    def summary(self) -> dict[str, Any]              # id/title/chars/source/truncated

@dataclass(frozen=True, slots=True)
class ContextPlan:
    policy: ContextPolicy
    sections: tuple[ContextSection, ...] = ()
    skipped: tuple[dict[str, str], ...] = ()         # {"id": ..., "reason": ...}
    @property
    def total_chars(self) -> int
    @property
    def tokens_estimate(self) -> int                 # total_chars // 2
    def render(self) -> str                          # 各 section 顺序拼接
    def snapshot(self) -> dict[str, Any]             # policy dict + sections + skipped + totals

@dataclass(frozen=True, slots=True)
class ContextRequest:
    tenant_id: str
    run_id: str
    conversation_id: str | None = None
    workspace_id: str | None = None
    goal: str = ""
    title: str = ""
    skill_id: str | None = None
    skill_markdown: str | None = None   # run 快照正文
    memory_block: str = ""              # services 已召回的块

class SectionProvider(Protocol):
    section_id: str
    async def build(self, request: ContextRequest) -> ContextSection | None: ...

class ContextPipeline:
    def __init__(self, policy: ContextPolicy, *, providers: Sequence[SectionProvider] = ()) -> None
    def register(self, provider: SectionProvider) -> None   # 运行期可插拔
    async def build(self, request: ContextRequest) -> ContextPlan
```

build() 语义:按 `policy.normalized_order()` 遍历;

- 开 `policy.enabled=False` → 返回空 plan(所有段 skipped,reason=`policy_disabled`);
- 该 id 未注册 → `not_registered`;provider 返回 None → `empty`;provider 抛异常 → `error:<type>`(降级,不阻断 run);
- 重复 register 覆盖同 id。

### 6.3 `agent_orchestrator/context/sandbox_files.py`

```python
SCOPE_CONVERSATION_PREFIX = "conversation:"
SCOPE_RUN_PREFIX = "run:"
SCOPE_TENANT_PREFIX = "tenant:"

def scope_key_for(tenant_id: str, *, conversation_id: str | None = None, run_id: str | None = None) -> str

class WorkspaceFiles(Protocol):
    async def read_text(self, tenant_id: str, scope_key: str, path: str) -> str | None: ...
    async def write_bytes(self, tenant_id: str, scope_key: str, path: str, data: bytes) -> None: ...
    async def find(self, tenant_id: str, scope_key: str, names: Sequence[str], *, max_depth: int) -> list[str]: ...

@dataclass
class SandboxWorkspaceFiles:
    sandbox: Sandbox                 # common.clients.sandbox.Sandbox
    ttl_seconds: int = 900
    _ids: dict[tuple[str, str], str] = field(default_factory=dict)  # 进程内 ensure 缓存
    async def read_text(...)
    async def write_bytes(...)
    async def find(...)              # 走 exec(find ...),只返回 /workspace 下绝对路径
```

`ensure(tenant_id, scope_key=..., ttl_seconds=..., run_id=...` 与 tool-service 同参,`read_file/write_file/exec` 都带 tenant_id + sandbox_id。

### 6.4 `agent_orchestrator/context/agents.py`

```python
@dataclass(frozen=True, slots=True)
class AgentsDocument:
    path: str            # 沙箱内绝对路径,如 /workspace/AGENTS.md
    content: str
    original_chars: int
    truncated: bool

class AgentsMdProvider:                  # section_id = "agents_md"
    def __init__(self, files: WorkspaceFiles, policy: ContextPolicy) -> None
    async def build(self, request: ContextRequest) -> ContextSection | None

async def discover_agents_documents(files, policy, *, tenant_id, scope_key) -> list[AgentsDocument]
```

发现规则:文件名取 `policy.agents_md_names`;范围 `/workspace` 根 + 相对深度 ≤ `agents_md_max_depth`(`find -maxdepth`),排序(浅→深,同层按路径字典序),最多 `agents_md_max_files` 个;单文件截前 `agents_md_file_max_chars` 并标 `truncated`,总量超 `agents_md_max_chars` 时后续文件丢弃并在 section 里注明。section content = 每个文件 `### <path>\n<content>`,title=「工作区规则(AGENTS.md)」,source=逗号拼接的相对路径。

### 6.5 `agent_orchestrator/context/skills.py`

```python
@dataclass(frozen=True, slots=True)
class SkillCard:
    skill_id: str
    title: str
    description: str
    body_chars: int
    card: str          # 注入系统提示的卡片文本(~100 token)

def build_skill_card(skill_id: str, markdown: str, *, max_chars: int) -> SkillCard
def skill_body_page(markdown: str, *, offset: int = 0, limit: int = 4000) -> tuple[str, int, bool]
    # 返回 (片段, 正文总字符数, 是否还有下一段)

class SkillCardProvider:                 # section_id = "skills"
    def __init__(self, policy: ContextPolicy) -> None
    async def build(self, request: ContextRequest) -> ContextSection | None

def build_load_skill_tool(session_factory, *, policy: ContextPolicy) -> ToolSpec
    # 工具名 load_skill;参数 offset:int=0, limit:int(默认 policy.skill_body_page_chars)
    # handler: current_tool_context().run_id → 读 runs.skill_system_prompt 分页返回;
    #          追加 run.skill.loaded.v1 事件;risk=READ
```

卡片格式(固定):标题、技能 id、正文体量、「需要时调用 load_skill 加载正文(分页)」;frontmatter 的 `description` 若解析得到则优先于首段摘要。

### 6.6 `agent_orchestrator/context/offload.py`

```python
@dataclass(frozen=True, slots=True)
class OffloadedOutput:
    content: str                       # 回填进 tool 消息的内容(引用信封或原文)
    record: dict[str, Any] | None      # offloaded/original_chars/path/chars;未 offload 为 None

class ToolOutputOffloader:
    def __init__(self, files: WorkspaceFiles, policy: ContextPolicy) -> None
    async def maybe_offload(self, tool_name: str, output: str) -> OffloadedOutput
    async def read(self, path: str, *, offset: int = 0, limit: int = 4000) -> tuple[str, int, bool]
    @property
    def prefix(self) -> str            # "/workspace/" + policy.offload_dir

def build_read_offloaded_tool(offloader: ToolOutputOffloader) -> ToolSpec
    # 工具名 read_offloaded;参数 path, offset:int=0, limit:int=4000;risk=READ
```

offload 判定:输出长度 ≤ threshold 或 `offload_enabled=False` 或取不到 ToolContext/沙箱写入失败 → 原样返回;否则写文件 `/workspace/<offload_dir>/<run_id>/<tool>-<sha8>.txt`,返回引用信封:

```json
{
  "ok": true,
  "offloaded": true,
  "tool": "read",
  "path": "/workspace/.agent/offload/<run>/read-ab12cd34.txt",
  "chars": 21344,
  "bytes": 21890,
  "sha256": "...",
  "truncated": true,
  "preview": "<前 offload_preview_chars 字符>",
  "note": "结果已完整落盘;需要细节时调用 read_offloaded(path=...) 分段读取。"
}
```

`read_offloaded` 只允许读 `prefix`(本 policy 的 offload 目录)下的路径,越界返回 JSON 错误。

## 7. 接线点

### 7.1 配置(`core/config.py`)

新增(默认值即"开启但无沙箱时自动降级"):

| 键                                                  | 默认                              | 含义                       |
| --------------------------------------------------- | --------------------------------- | -------------------------- |
| `context_pipeline_enabled`                          | True                              | 管线总开关                 |
| `context_section_order`                             | `["agents_md","skills","memory"]` | 注入顺序                   |
| `context_agents_md_enabled`                         | True                              | AGENTS.md 注入             |
| `context_agents_md_names`                           | `["AGENTS.md"]`                   | 文件名                     |
| `context_agents_md_max_chars` / `_file_max_chars`   | 4000 / 2000                       | 预算                       |
| `context_agents_md_max_depth` / `_max_files`        | 2 / 8                             | 发现范围                   |
| `context_offload_enabled`                           | True                              | 大结果 offload             |
| `context_offload_threshold_chars`                   | 8000                              | 触发阈值                   |
| `context_offload_preview_chars`                     | 1200                              | 预览长度                   |
| `context_offload_dir`                               | `.agent/offload`                  | 工作区相对目录             |
| `context_offload_max_chars`                         | 200000                            | 落盘硬上限(超出截断并标注) |
| `context_skill_loading_enabled`                     | True                              | SKILL.md 渐进加载          |
| `context_skill_card_max_chars` / `_body_page_chars` | 400 / 4000                        | 卡片/正文页                |
| `context_sandbox_ttl_seconds`                       | 900                               | 复用沙箱 TTL               |

### 7.2 容器(`core/container.py`、`core/container_assemblies.py`)

实现位置:`AppContainer.__init__`(:158)构造 `policy = policy_from_settings(settings)` 并预置 `context_pipeline=None`/`tool_offloader=None`;`_assemble`(:225)在沙箱/工作区就绪后调用 `_assemble_context()`(:460-495)。

- `_adopt`(:186):测试在 `create_app` 之后替换 settings 时重建 policy(与既有注入覆盖语义一致)。
- `_assemble_context` 只装配、不读库:
  - `self._publish("context_policy", policy)`;
  - `files = SandboxWorkspaceFiles(self.sandbox_client, ttl_seconds=settings.context_sandbox_ttl_seconds)`(仅 `sandbox_client` 存在时);
  - `settings.context_pipeline_enabled` 为真时建管线:有沙箱 → `[AgentsMdProvider(files, policy), SkillCardProvider(policy), MemorySectionProvider()]`,无沙箱 → `[SkillCardProvider(policy), MemorySectionProvider()]`(agents_md 段进 `not_registered`);
  - `pipeline` 发布为 `context_pipeline`(总开关关时为 `None`);`ToolOutputOffloader(files, policy)` 发布为 `tool_offloader`(无沙箱或 offload 关时为 `None`);
  - 无沙箱时执行节点收到 `offloader=None`,回填行为与改造前一致,启动不因此失败。
- `_assemble_tools`(:361-372):extras 装配条件放宽为「mcp/skill_execution/memory **或** policy.skill_loading_enabled **或** policy.offload_enabled」。
- `container_assemblies._extras_factory`(:111-124):**仅当 `current_tool_context()` 有 `run_id`**(run 路径)时追加 `build_load_skill_tool(factory, policy=policy)` 与 `build_read_offloaded_tool(offloader)`;chat 路径不带这两个工具(没有 run 正文、也不会 offload)。
- `_assemble_graph`(:571/:584):`build_run_graph(..., offloader=self.tool_offloader)`。
- 容器只装配,**不读库**:policy 快照与 AGENTS.md 内容的读取发生在 services/节点调用边界。

### 7.3 图(`graph/state.py`、`graph/ports.py`、`graph/nodes.py`、`graph/plan_nodes.py`、`graph/builder.py`)

- `RunGraphState` 新增:`context_block: str`、`context_snapshot: dict`(读取一律 `.get`,旧 checkpoint 兼容)。
- `graph/ports.py` 新增 `OffloadPort` Protocol(`async def maybe_offload(tool_name, output) -> OffloadedOutputLike`,只依赖 `graph.tool_results` 的形状:`.content` + `.record`);`build_run_graph(..., offloader: OffloadPort | None = None)` 传给 `make_execute_node`/`make_tool_exec_node`。
- 节点提示词:新增 `_context_block(state)` 渲染 state 的 `context_block`;`_execute_messages`/`plan_node`/`verify`/`deliver` 在有 `context_block` 时用它,否则回退旧行为(`_skill_block + _memory_block`),保证旧 checkpoint/测试不受影响。
- 工具结果:execute/tool_exec 在 `toolkit.execute` 之后调 `offloader.maybe_offload`,`_record` 增加 `offloaded`、`offload_path`、`original_chars` 字段(缺省 False/None)。

### 7.4 services(`services/run_execution.py`、`services/context_service.py`、`services/run_execution_approvals.py`)

- 新增 `services/context_service.py`(`CONTEXT_ASSEMBLED_EVENT="run.context.assembled.v1"`):
  - `async def build_run_context(session, run, *, pipeline: ContextPipeline, memory_block: str = "") -> RunContext | None`:拼 `ContextRequest` 并 `pipeline.build`;policy 关闭/管线异常(记 warning)/渲染为空 → `None`;
  - `RunContext`(frozen):`block`、`snapshot`、`tokens_estimate`、`section_ids`,property `injected`;
  - `async def append_context_event(session, run, context) -> None`:payload = snapshot + `attempt` + `tokens_estimate` + `section_ids`,写失败只 warning、不阻断 run。
- `services/run_execution.py` 的 `_invoke_plan(..., context: ContextPipeline | None = None)`:在 `state["memory_block"]` 之后构建上下文,写入 `state["context_block"]`/`state["context_snapshot"]`,进图前 `append_context_event`。`plan_run/retry_run/resume_run` 三入口加同名参数透传;**四条调用链**都传 `container.context_pipeline`:`api/v1/run_actions.py`(三端点)、`api/v1/conversation_messages.py`(post/stream 两端点 → `services/conversations.py` → `conversations_persist._task_path` → `plan_run`)、`services/jobs_worker.py`(plan/resume/retry 分支);`api/deps.py` 的 `get_context_pipeline` 用 `getattr(container, "context_pipeline", None)` 兜底。
- `services/conversations.py` 的**对话压缩两处**改读 `policy_from_settings(settings).compaction()`(policy 是 settings 的运行期投影,默认值不变;`context_compaction_enabled=False` 时关闭)。
- `services/run_execution_approvals.py` 的 `_tool_event_payload` 增加 `offloaded`/`offload_path`/`original_chars`(`offload_path` 进 payload 前截 512 字符)。

## 8. 状态、事件与可观测性

| 事件                         | 时机                                    | payload(要点)                                                                                                                 |
| ---------------------------- | --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| `run.context.assembled.v1`   | `_invoke_plan` 进图前,pipeline 有输出时 | `attempt`、`policy`(含 fingerprint)、`sections`(id/title/chars/source/truncated)、`skipped`、`total_chars`、`tokens_estimate` |
| `run.skill.loaded.v1`        | `load_skill` 工具每次调用               | `skill_id`、`offset`、`limit`、`chars`、`total_chars`、`truncated`                                                            |
| `run.tool.called.v1`(扩字段) | 既有                                    | 追加 `offloaded`、`offload_path`(≤512)、`original_chars`                                                                      |

验收口径:`run.context.assembled.v1` 里 `skipped` 说明为什么某个来源没进(未注册/空/策略关闭/异常),审计能回答「哪个技能/规则何时进了上下文」。

## 9. 测试计划

- `tests/test_context_policy.py`:默认值、settings 映射、order 归一化、fingerprint 稳定、to_dict 可 JSON。
- `tests/test_context_pipeline.py`:顺序、未注册/空/异常降级、disabled 空 plan、render/snapshot。
- `tests/test_context_agents.py`:Fake WorkspaceFiles;根+子目录发现、排序、预算截断、缺失文件、单文件截断、无沙箱降级。
- `tests/test_context_offload.py`:阈值边界、引用信封字段、路径/哈希、写入失败原样返回、`read` 越界拒读、`read_offloaded` 工具信封。
- `tests/test_context_skills.py`:卡片提取(frontmatter/标题/首段)、分页边界、load_skill 工具信封与事件、无 run 上下文报错。
- `tests/test_context_config.py`:18 个 `context_*` 配置的默认值、env 覆盖(bool/int/str/list)与显式入参优先。
- `graph` 侧:`tests/test_context_graph.py`(fake offloader 注入、`_record` 扩字段、context_block 优先/legacy 回退、DENY/CONFIRM 不 offload)。
- 服务侧:`tests/test_context_service.py`(plan_run 注入 state + 事件、pipeline 失败降级)。
- 容器侧:`tests/test_container_context.py`(管线段顺序、缺沙箱退化、开关关不发布、offload 透传、extras 的 run/chat 差异、builder 收到 offloader)。
- 既有测试(**不回改断言**):`tests/conftest.py` 的 `_SETTINGS_DEFAULTS` 钉住 `context_pipeline_enabled`/`context_skill_loading_enabled`/`context_offload_enabled=False`,旧用例继续验证改造前行为;新能力由 `tests/test_context_*.py` 显式打开。
- 回归:全量 `npx nx test agent-orchestrator` —— 822 passed / 0 failed,覆盖率 **约 87%**(本次 86.96%,门禁 82;`uv run pytest` 直跑口径 87.13%)。

## 10. 边界与风险

- **工具结果 >25K**:tool-service 先报错,offload 看不到——本期不解决,后续抬 `MAX_TOOL_CHARS` 或让 tool-service 直接回 offload 引用。
- **沙箱作用域不一致**:AGENTS.md/offload 与工具必须落在同一 scope_key;规则已在 `context/sandbox_files.py` 固定,跨服务靠约定 + 测试(scope_key_for 单测)。
- **注入预算抢占**:AGENTS.md/技能卡变长会挤占提示;每段都有预算,超预算先截断再标 `truncated`,不静默丢弃。
- **多租户**:只在租户 scope 的沙箱内读文件,不读宿主;`find` 只在 `/workspace` 下。
- **回放**:policy 只快照进事件,不参与图状态机;重跑同一 run 读同 state(checkpoint),不重算 AGENTS.md。

## 11. 分期

- **M1(已落地,2026-10-27)**:policy/pipeline/agents/offload/skills 模块 + 容器/图/services 接线 + 三个事件 + 测试(新增 9 个 `tests/test_context_*.py` 与 `test_container_context.py`,共 132 例)。
- M2(后续):tool-service 侧大结果直接落盘;按记忆相关性裁剪 AGENTS.md;上下文预算的自适应分配。

## 12. 实施记录(2026-10-27)

新增代码(均在 `services/agent-orchestrator/`):

| 文件                                             | 内容                                                                                              |
| ------------------------------------------------ | ------------------------------------------------------------------------------------------------- |
| `agent_orchestrator/context/policy.py`           | `ContextPolicy` / `CompactionPolicy` / `policy_from_settings` / 段常量                            |
| `agent_orchestrator/context/pipeline.py`         | `ContextPipeline` / `ContextRequest` / `ContextSection` / `ContextPlan` / `MemorySectionProvider` |
| `agent_orchestrator/context/sandbox_files.py`    | `scope_key_for`(与 tool-service 同规则)、`WorkspaceFiles`、`SandboxWorkspaceFiles`                |
| `agent_orchestrator/context/agents.py`           | `discover_agents_documents`、`AgentsMdProvider`                                                   |
| `agent_orchestrator/context/offload.py`          | `ToolOutputOffloader`、`read()`、`build_read_offloaded_tool`                                      |
| `agent_orchestrator/context/skills.py`           | `build_skill_card`、`skill_body_page`、`SkillCardProvider`、`build_load_skill_tool`               |
| `agent_orchestrator/context/__init__.py`         | 统一导出(32 个名字)                                                                               |
| `agent_orchestrator/services/context_service.py` | `build_run_context` / `append_context_event` / `RunContext`                                       |

改动:graph(state/ports/nodes/plan_nodes/deliver_nodes/builder)、core(config/container/container_assemblies)、services(conversations/conversations_persist/run_execution/run_execution_approvals/jobs_worker)、api(deps/conversation_messages/run_actions)。

事件(`append_event`,经既有 outbox 通道):`run.context.assembled.v1`、`run.skill.loaded.v1`,`run.tool.called.v1` 扩 `offloaded`/`offload_path`/`original_chars`。

验证(2026-10-27):`npx nx test agent-orchestrator` → 822 passed / 0 failed,覆盖率约 87%(86.96%);`npx nx lint agent-orchestrator`、`npx nx format`、`bash scripts/check-docs.sh` 通过(见交付说明)。

与设计的实现差异:

1. `ToolOutputOffloader` 无 `tool_call_id` 参数(无调用方需要);offload 文件名用 sha256[:8] 去重。
2. `AgentsMdProvider` 构造参数顺序为 `(files, policy)`;无沙箱时该段不注册,`skipped.reason=not_registered`。
3. `build_load_skill_tool` 与 `build_read_offloaded_tool` 只在 **run 上下文** 出现(chat 路径不挂,避免工具面噪声);`load_skill` 的 `truncated` 表示还有下一页。
4. `run.context.assembled.v1` 只在管线有输出时落;policy 快照随事件记录,checkpoint 不重算。
