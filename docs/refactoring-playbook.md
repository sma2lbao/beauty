# beauty-moon 重构标准流程（Refactoring Playbook）

> 版本 v1.6 · 最近更新 2026-10-25 · 生效日期 2026-10-24 · 维护者：重构 Lead（AI）+ 人工审批
> 适用范围：本仓库所有「结构重构」与「平台化补缺」类变更。产品功能开发不强制走本流程，但仍须遵守第 6 节门禁。
> 单一真源：本文件。工程约定冲突以根 AGENTS.md 为准；流程冲突以本文件为准。
> 数值口径：文中「现状基线」类数字是 2026-10-24 立项时的快照，只用于说明改造起点；任何时刻的最新数值以 docs/refactoring/baseline.md 与其波次实测段为准。

## 0. 一页纸

**目标**：在保持现有单 run 闭环（工具调用 → 审批 → 执行 → 事件 → SSE → UI）行为不变的前提下，把仓库提升到主流 agent 项目的工程水位：边界清晰、共享内核稳定、CI 可判定、文档单一真源、每一步可回滚。

**七阶段**（阶段 0–6；每个阶段有明确退出条件，未通过不得进入下一阶段）：

| 阶段 | 名称           | 核心产物                                        | 退出条件                         |
| ---- | -------------- | ----------------------------------------------- | -------------------------------- |
| 0    | 立项与范围冻结 | RFC                                             | 范围/验收/回滚方案获批           |
| 1    | 基线与护栏     | 基线表、门禁脚本、补齐的 characterization tests | 受影响项目全绿 + 基线入库        |
| 2    | 切片与依赖排序 | 路线图 + 每片任务（含 write scope）             | 每片单 PR 可审、相邻片无文件冲突 |
| 3    | 实施循环       | 每片 PR + 测试                                  | DoD 全满足 + 独立评审通过        |
| 4    | 验证与验收     | 验证报告                                        | 三层验证通过 + 指标对比达标      |
| 5    | 落地与观察     | 灰度/回滚记录                                   | 观察期无回归                     |
| 6    | 收尾与防回归   | 防回流规则、文档更新、复盘                      | 旧路径删除 + 门禁固化            |

**十条原则**（详见 2.2）：先基线后动手、单 seam、行为等价优先、依赖单向、契约先行、小步可回滚、验证前置、文档单一真源、可观测性不后置、无验收不重构。

**变更节奏**：一片 = 一个 seam = 一个 PR；单 PR diff 目标 ≤ 400 行；重构 PR 串行合并；每片可独立 revert。

**门禁**：受影响项目的 lint + typecheck + test（零网络）必须通过；改 ORM 必跑 alembic check；依赖变更只走 nx add/sync 或根 npm install；文档变更必须更新单一真源。

**产物清单**：RFC、基线表、路线图、每片 PR、验证报告、ADR、波次复盘。RFC/基线表/路线图/验证报告落 docs/refactoring/，ADR 落 docs/adr/；流程与模板就是本文件，执行状态在任务板。

## 1. 背景与边界

### 1.1 现状事实（勘察基线，2026-10-24）

- 形态：Nx 23.2.1 polyglot monorepo，15 个 Nx 项目。JS/TS 走 npm workspaces（apps/_、packages/_、tools/\*）；Python 走 uv workspace，共 10 个成员（packages/common、database、events + services/ 下 7 个 FastAPI 服务）。
- 规模：Python 源码约 2.82 万行（其中 agent-orchestrator 约 1.59 万行、70 个源文件）、pytest 用例 697 个（103 个测试文件，uv run pytest --collect-only 实测）；TS/TSX 源码 33319 行（236 文件，另有 2226 行 CSS）、vitest 用例 446 个（74 文件）。
- 已有工程质量：仓储层租户隔离、事务性 outbox、事件溯源 + SSE 断线续传、测试零网络、全仓 TODO 仅 5 处。这些是重构必须保住的资产，也是回归测试的基线。
- 结构性风险：apps/web/src/pages/chat.tsx 1313 行、mcp.tsx 941 行、components/console/sidebar.tsx 663 行；agent-orchestrator 单服务 70 个源文件、分层契约不完整；七个服务骨架重复；apps/web 与 apps/desktop 存在代码分叉；文档存在 architecture.md 与 architecture-v2.md 双版本、gap 报告与 feature-gap.md 重叠；无 CI（无 .github）；仅根 AGENTS.md 一份，无子树级约定。

### 1.2 为什么现在做

1. 单文件膨胀已经让并行开发与 AI 协作的冲突成本上升（同一文件多人/多 agent 改）；
2. 服务边界靠约定而非结构表达，新增第八个服务时复制粘贴不可避免；
3. 文档多版本共存，决策来源不可追溯，agent 读错文档的代价远高于人；
4. 没有 CI 门禁，重构只能依赖人工全量验证，规模上不去；
5. gap-2026-10/00-summary.md 已给出 P0/P1/P2 缺口清单，但缺少「怎么改、按什么顺序改、如何验收」的标准流程——本文件补的就是这一层。

### 1.3 三类工作的边界

| 类型       | 定义                                       | 是否走本流程             | 典型例子                                      |
| ---------- | ------------------------------------------ | ------------------------ | --------------------------------------------- |
| 结构重构   | 不改变外部行为，改变代码组织/边界/复用方式 | 是                       | 拆分 chat.tsx、抽取服务骨架、迁移目录         |
| 平台化补缺 | 补齐工程能力，通常新增横切设施             | 是（+ ADR）              | 最小 CI、traceId、usage 生产者、黄金 run 回归 |
| 产品功能   | 新增或改变用户可见行为                     | 否（借用门禁与 PR 规范） | Todo/Goal、文件树、模型选择                   |
| 缺陷修复   | 修复错误行为                               | 否（但需回归测试）       | SSE 断线重连 bug                              |

判定规则：**如果 PR 改变了 API 响应、事件 schema、SSE 事件序列或数据库列，它就不是纯结构重构**，必须按「契约变更」处理（第 4.5 节 + ADR）。

### 1.4 非目标

- 不重写框架、不换技术栈（React 19 / FastAPI / Nx / uv 均保留）；
- 不做大爆炸式目录迁移；不做"顺手美化"式格式化 PR；
- 不为了更优雅改变运行时行为；不在重构 PR 中夹带功能变更；
- 不引入需要外部服务才能跑通本地测试的新依赖（继续保持零网络测试）；
- 不修改生成物、锁文件、服务 .env（AGENTS.md 已禁止）。

## 2. 行业基线：主流 agent 项目的工程做法

### 2.1 对照表

下表来自公开文档与仓库内 docs/gap-2026-10/02a-claude-codex.md、02b-oss-harnesses.md 的横评归纳，只提取与「重构/演进」相关的做法：

| 项目        | 约定文件                        | 权限与隔离                           | 扩展机制                                       | 上下文管理           | 可观测性               | 发布形态             |
| ----------- | ------------------------------- | ------------------------------------ | ---------------------------------------------- | -------------------- | ---------------------- | -------------------- |
| Claude Code | CLAUDE.md（可分目录）+ 斜杠命令 | allow/deny/ask 分层权限、hooks、沙箱 | MCP、Skills、subagent（独立上下文）、Agent SDK | 自动压缩、子代理隔离 | 会话级 usage、成本提示 | CLI + SDK/headless   |
| Codex CLI   | AGENTS.md（项目约定格式）       | 沙箱 + 审批策略（只读/自动/全权）    | MCP、嵌套子代理                                | 自动压缩、长任务分段 | 事件流、日志           | CLI/exec（可脚本化） |
| OpenHands   | 仓库级配置                      | 容器沙箱、动作白名单                 | 工具插件、runtime 抽象                         | 事件流 + 状态        | 轨迹回放               | 本地/云端 runtime    |
| SWE-agent   | 仓库配置                        | 容器隔离                             | 工具接口                                       | 轨迹预算             | 轨迹日志               | CLI/批处理           |
| Aider       | 约定文件 + 仓库地图             | 本地 git 自动提交                    | 模型可插拔                                     | repo map             | token 用量             | CLI                  |
| Gemini CLI  | GEMINI.md                       | 工具确认                             | MCP、扩展                                      | 上下文压缩           | 会话统计               | CLI                  |

共同点（跨项目稳定出现的做法）：

1. **单一约定文件 + 子树覆盖**：根级 AGENTS.md/CLAUDE.md 定义全局规则，子目录可再放一份覆盖局部约定；agent 先读最近的约定文件。
2. **边界靠结构表达**：runtime / tools / provider / UI / storage 分层，provider 可插拔；新能力通过接口接入而不是改核心分支。
3. **小步、可验证、可回滚**：每次只改一个接缝，独立 PR，能力用开关包裹。
4. **质量门禁自动化**：lint / typecheck / test / build 是合并前置条件，而不是"有空再跑"。
5. **可观测性内建**：traceId 贯穿一次 run，usage/成本可见，失败可定位到阶段。
6. **安全默认**：沙箱 + 审批策略 + 最小权限，危险动作默认拒绝、可显式放行。
7. **文档即代码**：架构决策有 ADR/文档，与代码同 PR 更新，过期文档会归档而不是继续堆。
8. **评测驱动**：golden run / regression suite 作为"重构没有破坏行为"的证据。
9. **协议/契约优先**：工具 schema、事件 schema、API schema 是跨模块的稳定边界。
10. **版本与发布纪律**：可复现构建、变更记录、明确回滚方式。

### 2.2 本仓库的落地原则（P1–P10）

| 编号 | 原则                                                                             | 判定方式                                              |
| ---- | -------------------------------------------------------------------------------- | ----------------------------------------------------- |
| P1   | 先基线后动手：任何重构前先有可运行的测试与指标基线                               | 基线表 + 门禁脚本存在且绿                             |
| P2   | 单 seam：一次只改一个接缝，主分支始终可构建、可运行                              | 每个 PR 只触及一个模块边界                            |
| P3   | 行为等价优先：先保持行为不变，再谈优化                                           | 契约快照/黄金路径不变                                 |
| P4   | 依赖单向：apps/services → packages；共享库不得 import 服务代码                   | import 边界检查 + 架构测试                            |
| P5   | 契约先行：多方消费的接口先冻结（pydantic schema / TS 类型 / 事件信封），再搬实现 | PR 中含契约变更说明与兼容期                           |
| P6   | 小步可回滚：单 PR ≤ 400 行、可独立 revert；跨服务变更用 flag                     | PR 体积检查 + 回滚剧本                                |
| P7   | 验证前置：本地能跑的门禁必须在提交前跑过，不接受"CI 会跑"                        | PR 描述附命令与输出                                   |
| P8   | 文档单一真源：一项决策一个 ADR；架构/流程文档只有一个当前版本                    | docs/README.md 索引唯一，docs/refactoring/ 只留活文档 |
| P9   | 可观测性不后置：重构不得删除或弱化 traceId、结构化日志字段、事件 id              | 日志/事件 schema diff 审查                            |
| P10  | 无验收不重构：每项有 owner、指标、验收标准、回滚方案                             | 任务板字段齐全，缺一不立项                            |

## 3. 角色、协作与任务板

### 3.1 角色

| 角色        | 职责                                         | 写权限                         | 交付物                    |
| ----------- | -------------------------------------------- | ------------------------------ | ------------------------- |
| Lead        | 立项、切片、排期、最终验收、跑全量门禁、合并 | 根配置、docs、任务板、共享文件 | RFC、路线图、验收记录     |
| Auditor     | 只读勘察，产出带 file:line 的证据            | 仅 .cache/refactor-audit/\*.md | 审计报告（≤15 条候选）    |
| Implementer | 按 write scope 独占实现 + 测试               | 任务声明的目录                 | PR + 测试                 |
| Reviewer    | 独立评审 diff，不写生产代码                  | 无（只评论）                   | 评审意见（阻断项/建议项） |
| Verifier    | 跑门禁、做前后基线对比、回归验证             | 无                             | 验证报告                  |

说明：本项目由 AI Agent Teams 执行，一人可兼多角色，但**同一 PR 的实现者与评审者必须是不同的 agent/会话**；Lead 负责最终签字。

### 3.2 任务板协议

每个任务必须有：subject（动词开头）、description（背景 + 验收标准 + 明确不做什么）、write_scopes（工作区相对路径前缀）、blocked_by（依赖任务）、owner。

状态机：pending →（claim）in_progress →（complete）completed；被阻塞时保持 pending 并写 blocked_by。任务完成后必须留下：PR 链接/commit、门禁命令与结果、基线对比。

### 3.3 并发与冲突规则

1. 同一文件同一时间只允许一个 writer；write_scopes 重叠的任务不得并行（阻塞关系写入任务板）。
2. 以下共享文件/目录只由 Lead 串行修改：根 package.json、根 pyproject.toml、nx.json、tsconfig.base.json、vitest.config.mts、AGENTS.md、docs/ 索引、package-lock.json、uv.lock。
3. 功能开发与重构并行时，重构 PR 优先保证"主干可发布"，冲突由 Lead 决定 rebase 或让路。
4. 生成物与缓存目录（dist、node_modules、.nx、.venv、reports、coverage）不属于任何 write scope。
5. 跨服务契约变更（事件 schema、HTTP API、SSE 事件）单独成片，不与其他重构混在一个 PR。
6. 共享工作树禁止 `git stash` / `git reset --hard` / `git checkout -- <他人文件>`：它们会静默吞掉并行成员的改动（W2 教训：回滚演练误伤 R-25 整批改动）。回滚演练一律用 `git diff <sha>^ <sha> > p && git apply -R --check p`，或 `git revert --no-commit` 后立即 `git revert --abort`。

### 3.4 证据与沟通规范

- 结论必须带 file:line；区分「事实」「推断」「建议」，推断必须标注依据。
- 回传摘要 ≤ 40 行，细节留在报告文件；不粘贴大段日志，只贴关键错误行/结果尾部。
- 禁止模糊表述（"大概""可能有问题"）；要么给出可复现命令，要么标为待验证。
- 决策必须记录理由（为什么选 A 不选 B），理由缺失的决策视为未完成。

## 4. 七阶段标准流程（阶段 0–6）

### 4.0 阶段总览

| 阶段   | 目的                             | 关键动作                     | 退出条件                | 建议时长      |
| ------ | -------------------------------- | ---------------------------- | ----------------------- | ------------- |
| 0 立项 | 明确改什么、不改什么、怎么算成功 | 写 RFC、风险登记、成功指标   | RFC 获批，任务树建好    | 0.5–1 天      |
| 1 基线 | 让"是否破坏行为"可判定           | 采集指标、补测试、建门禁脚本 | 门禁全绿 + 基线入库     | 1–2 天        |
| 2 切片 | 把大改拆成可独立验证的小片       | 依赖图、seam 识别、任务拆分  | 每片 ≤ 2 天、单 PR 可审 | 0.5–1 天      |
| 3 实施 | 逐片改代码                       | TDD、自检、评审、合并        | DoD 全满足              | 按片计        |
| 4 验证 | 证明行为等价且指标改善           | 三层验证、指标对比、签收     | 验证报告通过            | 每波次 0.5 天 |
| 5 落地 | 安全上线并可回滚                 | flag、灰度、观察、回滚剧本   | 观察期无回归            | 1–3 天        |
| 6 收尾 | 防止旧结构回流                   | 删旧路径、架构测试、文档同步 | 防回流规则生效          | 0.5 天/波次   |

### 4.1 阶段 0：立项与范围冻结（RFC）

输入：审计报告、现有架构文档、问题清单、可量化指标。

动作：

1. 写 RFC（位置 docs/refactoring/rfc/NNN-<slug>.md，模板见 11.1）。
2. 明确 in-scope / out-of-scope；列出被影响的契约（API/事件/DB/前端路由）。
3. 登记风险：可能性 × 影响 × 缓解措施 × 回滚方式。
4. 定义成功指标（结构/质量/行为三类，见第 7 节）。
5. 在任务板建任务树：每片一个任务，标注 write scope 与 blocked_by。
6. 若本项会改共享图结构（nx.json targetDefaults、项目依赖边、根 tsconfig），预先列出会被牵连的既有 target 与行为对照（W0 教训：R-04 加边牵连 test.dependsOn [^build]）。
7. 跨语言/多包版本必须机器断言：JS 侧交给 nx release（version + changelog + tag），Python 侧由 scripts/check-versions.sh 校验 pyproject/包常量一致，不强行把两种生态塞进同一套编排（W4 教训：单版本号 0.1.0 一次对齐 18 处，靠脚本而非人眼）。
8. 预算里显式留 30% 给「门禁首次真跑暴露的既有债」：W2 计划 10 项，实际 6 个配套修复（CI 模板表达式损坏、Node 下限、alembic 改 Postgres、模型/迁移漂移、isort 不一致、跨平台进程组）占掉近半工时。

产物：RFC、任务树、风险登记表。

退出条件：RFC 经人工（或 Lead + 独立评审 agent）批准；所有范围项都有验收标准与回滚方案。

反模式：没有 RFC 直接改目录；RFC 里只写"重构 xx 使其更清晰"而没有判定标准；范围项超过 10 个还硬塞进一个波次。

### 4.2 阶段 1：基线与护栏

目的：让后续每一步都能回答"我有没有把东西改坏"。

动作：

1. **指标基线**：结构指标（文件行数 Top-N、目录文件数、循环依赖、跨层 import）、质量指标（lint/typecheck 错误数、测试数、覆盖率）、行为指标（pytest/vitest 全绿、黄金路径）、性能指标（构建时长、bundle 体积、测试时长）。
2. **characterization tests**：对将被改动的关键路径，先补"锁行为"的测试（不追求覆盖率数字，追求关键分支）。本仓优先补：SSE 事件序列、审批决策、outbox 重试、租户隔离 404、chat 发送/流式渲染。
3. **门禁脚本**：新增 scripts/refactor-gate.sh，输入项目名，执行该项目的 lint + typecheck + test（零网络），输出 PASS/FAIL 与耗时；CI 落地前它就是唯一可信门禁。
4. **稳定基线快照**：契约快照（OpenAPI/pydantic schema/事件信封字段）与前端关键页面的 DOM 快照/截图基线。

产物：docs/refactoring/baseline.md（基线表）、scripts/refactor-gate.sh、契约快照文件。

退出条件：受影响项目门禁全绿；关键路径有 characterization 测试；基线表已提交并标注采集命令。

反模式：先重构后补测试；用"覆盖率 80%"代替关键路径覆盖；基线数据没有采集命令导致不可复现。

### 4.3 阶段 2：切片与依赖排序

动作：

1. **画依赖图**：npx nx graph + 按包统计 import 扇入扇出；Python 用 ruff 的 import 规则 + grep 交叉验证。
2. **识别 seam**（接缝）：
   - 扇入低（被少量模块引用）的模块优先；
   - 已有测试覆盖的模块优先；
   - 纯数据/纯函数模块优先于带 IO 的模块；
   - 契约稳定（类型/schema 已冻结）的边界优先。
3. **切片**：S（≤1 天、≤150 行 diff）、M（≤2 天、≤400 行）、L（>2 天，必须再拆，不允许 L 直接开工）。
4. **排序启发式**：先低风险后高风险；先稳定被依赖方（共享库/基座）再改依赖方；先补测试再搬代码；先删死代码再抽公共层。
5. **冲突预判**：同一片涉及的文件写成 write scopes，任务板自动提示重叠；重叠则串行。
6. **产出路线图**：docs/refactoring/roadmap.md，按波次（Wave）组织，每波 3–6 片。

退出条件：每片有目标结构图/前后对比、验收命令、回滚方式；相邻片无文件冲突；L 已拆分。

反模式：按"文件目录"切片而不是按"接缝"切片；一次动 5 个服务；先改共享库再找谁在用。

### 4.4 阶段 3：实施循环（每片 8 步）

1. 同步：确认主干绿（lint/typecheck/test 基线）。发现与 RFC 假设冲突的既有行为时，先 git stash 对照留证据，再决定处置（W0 教训）。
2. 测试先行：先写/改 characterization 测试或新增失败测试。
3. 最小改动：一次只移动一个 seam；先复制后切换再删除（Strangler Fig），或先加抽象后替换实现（Branch by Abstraction）。
4. 本地门禁：scripts/refactor-gate.sh <项目>；Python 迁移变更追加 alembic check。
5. diff 自检：≤400 行、无无关格式化、无生成物、无锁文件、无 .env；命名与目录约定符合 AGENTS.md。
6. 提交：conventional commits（refactor:<scope>: <what>），一个提交只做一件事。
7. 独立评审：Reviewer 按 11.3 清单评审；阻断项修完才可合并。
8. 合并前重跑：在最新主干上重跑门禁；记录结果到 PR/任务。

Definition of Done（每片）：

- 新结构落地且旧路径/旧实现删除（或明确标注下一步删除时间）；
- 行为测试全绿，契约快照无意外变化（有意变化须 ADR + 兼容期）；
- lint/typecheck 零错误；
- 新增/修改代码有测试；覆盖关键分支；
- 文档（架构图/README/AGENTS.md 相关段落）同步更新；
- PR 描述含：动机、前后对比、验证命令与结果、回滚方式、遗留项。

### 4.5 阶段 4：验证与验收

三层验证：

1. **单元/集成层**：pytest 724（10 个项目）+ vitest 498（web 324 / desktop 59 / design 100 / console-ui 6 / site 9）全绿；零网络（Settings(\_env_file=None) 且钉住外部开关）。
2. **契约层**：OpenAPI 快照、pydantic schema、事件信封字段、SSE 事件序列（meta/delta/done）不变或按兼容策略演进。
3. **黄金路径层**：golden run（一次完整 run：创建会话→发消息→工具调用→审批→执行→事件→SSE→UI 可见）；关键页面冒烟；UI 视觉基线对比。

验收动作：

- 指标前后对比表（第 7 节指标），退化超阈值即阻断；
- 人工/独立 agent 走查关键路径；
- 跨平台失败（只在 CI 红、本地绿）先定位实现语义差异（进程组/信号/管道/时间精度/文件系统延迟），再考虑放宽超时；W2 的 desktop 三例最终是「取消只杀 sh 不杀孙进程」而非 flake；
- 环境差异排查（只在 `nx test` 红、直跑 `uv run pytest` 绿）：先比对任务环境——Nx 会注入项目 `.env` 与 target 的 env 前缀，`Settings(_env_file=None)` 挡不住；再怀疑并发/flake。W3 的 AO 两个 quota 用例连红两轮，实为 `.env` 让容器装配了真实 account URL、被禁网门禁拦下，不是并发干扰；
- Lead 签字，任务板标记完成。

### 4.6 阶段 5：落地、观测与回滚

- 后端变更优先用既有开关模式（如 OUTBOX_ENABLED、KNOWLEDGE_INGEST_ENABLED）或新增 flag 包裹；
- 合入顺序：先部署兼容代码 → 再切流量/开关 → 观察 → 再删旧路径；
- 观察项：错误率、P95 延迟、SSE 建连成功率、outbox 积压、关键日志字段（traceId 存在性）；
- 回滚剧本必须写在 PR 中：revert commit / flag off / 迁移不回滚但向前兼容；数据库变更遵循 expand-contract（第 5.2 节）；
- 观察期（至少 24h 或一个工作日）无回归才进入下一片。

### 4.7 阶段 6：收尾与防回归

1. 删除旧路径、死代码、临时适配层（单独 PR，不与其他改动混合）。
2. 加防回流规则：import 边界检查（Python 用 ruff TID/flake8-tidy-imports 或自写测试；TS 用 eslint import/no-restricted-paths 或 dependency-cruiser）、文件行数上限 lint、目录命名约定。
3. 更新文档单一真源；过期文档归档到 docs/archive/。
4. 波次复盘：哪些片超时、哪些冲突、哪些指标未达标；把教训写回本文件或 AGENTS.md。
5. 更新基线表（下一波次的起点）。

## 5. 重构模式库

### 5.1 通用模式

| 模式                              | 何时用                   | 本仓例子                               | 风险                 |
| --------------------------------- | ------------------------ | -------------------------------------- | -------------------- |
| Strangler Fig（绞杀者）           | 旧模块要整体替换         | 页面逐步从 chat.tsx 抽到 feature 目录  | 新旧并存期变长       |
| Branch by Abstraction             | 多调用方同时使用旧实现   | 服务骨架抽取、provider 抽象            | 抽象层选错方向       |
| Parallel Change / Expand-Contract | 数据模型/契约变更        | alembic 迁移、事件字段新增             | 回填不完整           |
| Extract Module                    | 单文件过大、职责混杂     | chat.tsx / sidebar.tsx 拆分            | 拆完仍是隐形耦合     |
| Move Boundary                     | 分层错误（api 直连 ORM） | Python 服务 api → service → repository | 事务边界被打破       |
| Introduce Facade                  | 消费方过多、内部实现易变 | web 的 \*-api.ts 统一到 http.ts 基座   | 门面变成新的上帝模块 |
| Characterization Test             | 无测试区域要改           | SSE、审批、outbox                      | 锁住 bug 行为        |
| Golden Master                     | 输出复杂、难写断言       | 事件序列、SSE 流、契约快照             | 基线本身可能错       |

### 5.2 Python 侧具体做法

1. **服务骨架抽取（service-kit）**：把七个服务重复的 main.py 骨架（lifespan、异常处理器、中间件、健康检查、配置基类、鉴权依赖）抽到共享库，服务只保留"注册哪些组件、按什么顺序"的声明式配置。规则：共享库不得 import 任何服务；用显式配置对象注入差异；先抽 2 个服务试点，稳定后再铺开。
2. **orchestrator 分层**：api/ 只做 HTTP 校验与依赖注入；业务编排进 services/；graph/ 只做状态机与节点；models/ 只做持久化实体；schemas/ 是唯一对外契约。禁止 api 直连 ORM，禁止 graph 直接发通知。
3. **事务与事件边界**：仓储不提交；事务边界只在 session_scope / UnitOfWork；跨服务事件只走 events.outbox；消费方用 mark_processed 幂等。重构时不得为了"少一层"把 outbox 调用移出事务。
4. **迁移策略**：expand（加列/加表，双写或默认值）→ 回填 → 切换读取 → contract（删旧列/旧路径）。每个 PR 至多一个迁移；改 ORM 必须配迁移并跑 alembic check（AUTO_CREATE_TABLES=false 时缺迁移测试看不出来）。
5. **测试夹具收敛**：跨服务重复的 SQLite 文件库、临时对象目录、JWT 生成器抽到共享测试工具包（不被生产代码 import），同时保留各服务零网络纪律。

### 5.3 TS/React 侧具体做法

1. **页面 feature 化**：pages/<feature>/index.tsx + components/ + hooks/ + api.ts + types.ts；页面文件只做组合，超过 300 行必须拆；容器（数据）与展示（UI）分离。
2. **api/hooks 分层**：每个后端资源一个 api 模块（conversations/runs/workspace/knowledge/account），统一走 src/lib/http.ts（错误契约、401 refresh 单飞、取消、超时）；react-query 只做缓存与状态，不在组件里手写 fetch。
3. **SSE 基座**：把 /messages/stream 的 meta/delta/done 解析、重连、取消抽成独立模块 + hook，页面不直接处理 ReadableStream。
4. **web/desktop 共享**：把与运行环境无关的部分（api 客户端、类型、hooks、console 组件）抽成共享包（如 @beauty/console-core）；desktop 只保留本地运行时适配层（进程/文件/命令），web 保留云端适配层。先抽契约最稳定的 api/types 层。
5. **design 包边界**：@beauty/design 只放无业务语义的组件与令牌（bd- 前缀 + --bd-\*）；apps/web 只组合，不再本地维护组件副本；Tailwind 用法与 design 令牌的边界写进 README。
6. **按路由拆包**：Vite 手动分包或路由懒加载，先建立 bundle 基线（立项时为单 chunk 737954B + CSS 77514B、0 处 lazy/Suspense；W1 起路由级拆包，现值见 docs/refactoring/baseline.md）再优化，避免"优化完更慢"。

### 5.4 跨切面

- **配置**：端口、健康检查路径、.env.example 保持一致；新增配置项必须有默认值且测试零网络。
- **脚本**：scripts/ 统一入口；新增脚本必须幂等、带 --help、失败退出码非 0。
- **文档**：见第 12 节文档治理。

## 6. 质量门禁（本仓可执行命令矩阵）

### 6.1 变更类型 → 必跑门禁

| 变更类型       | 必跑命令                                                                                      | 通过标准                                 | 备注                                                                                   |
| -------------- | --------------------------------------------------------------------------------------------- | ---------------------------------------- | -------------------------------------------------------------------------------------- |
| TS/React 代码  | npx nx lint <项目>；npx nx typecheck <项目>；npx nx test <项目> -- --run                      | 0 error；测试全绿                        | 缺 typecheck target 的项目跳过该步；**必须加 -- --run**，否则 vitest 进 watch 模式挂住 |
| 包公共 API     | 上述 + npx nx build <项目>                                                                    | 构建成功、类型声明无变化或已记录         | 消费方需要重新验证                                                                     |
| Python 代码    | npx nx lint <项目>；npx nx test <项目>；uv run ruff format --check <包名> tests（项目目录内） | ruff 0 error；pytest 全绿；格式化无 diff | npx nx format 会改写文件，门禁场景用 --check                                           |
| ORM/迁移       | cd services/<svc> && uv run alembic check；npx nx test <svc>                                  | 模型与迁移一致；测试通过                 | 测试走 create_all，看不出缺迁移；AUTO_CREATE_TABLES=false                              |
| Python 依赖    | npx nx run <项目>:add/remove；npx nx run <项目>:sync                                          | uv.lock 已更新；共享 .venv 同步成功      | 不要直接 pip install；测试依赖必须写进本项目 dev group                                 |
| JS 依赖        | 改项目 package.json；根目录 npm install                                                       | package-lock.json 更新且无意外版本漂移   | 受限环境用 npm_config_cache=$PWD/.npm-cache                                            |
| 端口/配置/脚本 | scripts/dev.sh --status；相关项目测试                                                         | 7 个服务 + web 健康检查通过              | 缺 .env 时会自动复制 .env.example                                                      |
| 跨服务契约     | 契约快照测试 + 三端（BFF/A 服务/B 服务）集成测试                                              | 快照按兼容策略演进                       | 事件 schema 必须至少一次投递语义不变                                                   |
| 文档           | 链接检查 + 事实核对（命令/路径/版本）                                                         | 无失效链接、无与代码冲突的命令           | W0 引入 markdown lint                                                                  |

### 6.2 一键门禁脚本（阶段 1 交付物）

脚本路径 scripts/refactor-gate.sh，接口：

    scripts/refactor-gate.sh <nx 项目名> [--no-test]

行为：

1. npx nx show project <项目名> 判断存在哪些 target；
2. 依次执行 lint → typecheck（若有）→ test（TS 加 -- --run）→ build（若有且改动公共 API）；
3. 每步打印耗时与 PASS/FAIL，最后汇总；任一失败退出码非 0；
4. 支持 --affected 模式：npx nx affected -t lint,typecheck,test --base=origin/main。

脚本必须能离线运行：不访问网络、不安装依赖、不写仓库外目录。

R-07 未落地期间，替代做法：按第 6.1 节命令矩阵逐条执行，把命令与结果尾部贴进 PR 的「验证」段（L1/L2 由 Lead 本地执行并留档）。

### 6.3 环境约束（易踩坑，必须写进 PR 模板）

- **watch 模式**：npx nx test <TS 项目> 默认 watch，脚本/agent 场景必须 -- --run；CI/门禁同样。
- **test-ci 禁用**：nx.json 配了 ciTargetName=test-ci（atomized task），本地需要 Nx Cloud，重构验证不要用它。
- **nx sync 抢占**：npx nx sync <项目> 会被 Nx 内置 sync-generators 命令拦截，必须 npx nx run <项目>:sync。
- **零网络**：Nx 会把项目 .env 注入任务环境，测试必须 Settings(\_env_file=None) 并显式钉住外部开关（llm_enabled/tools_enabled/outbox_enabled 等）；只做前者会让测试真的调用模型网关。
- **共享 venv**：uv run 会按当前项目 dev group 精确同步共享 .venv，测试依赖（pytest-asyncio、aiosqlite 等）必须写进本项目 dev group。
- **锁文件**：uv.lock / package-lock.json 只能通过包管理命令更新。
- **受限环境**：npm 缓存用 npm_config_cache=$PWD/.npm-cache；uv 缓存用 UV_CACHE_DIR=$PWD/.uv-cache、UV_PYTHON_INSTALL_DIR=$PWD/.uv-python。
- **Node 下限对齐传递依赖**：engines/.nvmrc 不能拍脑袋（jsdom 30.1.2 要求 ^22.22.2 || ^24.15.0 || >=26、undici 8.11 要求 >=22.19）；Node 20 下 vitest 直接 `TypeError: webidl.util.markAsUncloneable is not a function`。改版本要同步 .nvmrc / package.json engines / README / AGENTS.md / docs/local-dev.md。
- **alembic check 需要真 Postgres**：AO/account 迁移含 PG 专有 DDL，sqlite 会 `near "ALTER": syntax error`；docker service 的健康检查 `--health-cmd "pg_isready -U beauty"` 必须带引号，否则 docker 把 `-U` 当自己的 flag（`unknown shorthand flag: 'U' in -U`）。`alembic check` 能抓出模型/迁移漂移（W2 实测 account 索引、AO 唯一约束名），dev 的 create_all 看不见。
- **本地 lint ≠ CI lint**：ruff isort 的 first-party 分组取决于 venv 里的包集合，本地共享 venv 与 CI 的单项目 venv 可能不同（W2 实测 bff/AO 各 2 处 I001）。推送后必须看 CI 结论。
- **观测类调用必须 fail-open**：用量上报、指标、遥测这类旁路调用一律 `except Exception`（带 `# noqa: BLE001` 与注释），不能只捕 `httpx.HTTPError`——W3 实测 `UsageReporter.report` 只捕 HTTPError，禁网后异常从 `run_quota` 的 finally 逃出，把 run 直接判 failed。
- **安全门禁分两档**：生产依赖漏洞（npm audit --omit=dev、pip-audit）阻断合并；全量依赖漏洞只出报告不阻断——否则历史间接依赖会让 CI 永久红，反而没人看。
- **安全豁免必须带 owner + expires**：每条 allowlist 记录（security/audit-allowlist.json）写清编号/原因/负责人/到期日，由 scripts/check-audit-allowlist.cjs 校验到期并阻断过期豁免；没有到期日的豁免等于永久后门。
- **发布/版本/门禁类改动一律脚本断言**：18 处版本号、CHANGELOG 形态、compose 配置、镜像可构建，全部用脚本或 CI job 判定，不靠人眼扫 diff。
- **禁网门禁会改变异常形态**：`--disable-socket` 下传输层异常通常被 anyio 任务组包装成 `ExceptionGroup`（`unhandled errors in a TaskGroup`），按异常类型分支的捕获点会失效；排障时先看异常链最内层。

### 6.4 门禁分级

| 级别          | 触发     | 内容                                           | 阻断 |
| ------------- | -------- | ---------------------------------------------- | ---- |
| L1 本地提交前 | 每次提交 | 受影响项目 lint + typecheck + test             | 是   |
| L2 合并前     | PR       | L1 + build（公共 API）+ 契约快照 + 迁移检查    | 是   |
| L3 波次验收   | 每波结束 | 全仓 lint/typecheck/test + 黄金路径 + 指标对比 | 是   |
| L4 发布前     | 发布候选 | L3 + e2e 冒烟 + 回滚演练（至少验证 flag off）  | 是   |

CI 落地前 L2–L4 由 Lead 在本地按矩阵执行并记录；CI 落地后 L1/L2 交给 CI。

门禁命令的退出码必须显式采集：写成 `命令 > log 2>&1; echo EXIT=$?`，不要用 `命令 | grep`——管道退出码来自末端的 grep，W3 曾因此让一处 E501 lint 失败混过提交（branch 保护之外的本地门禁也同理）。

CI 结论类证据必须附 run 链接 + 逐 job 结论表，不得只写「CI 通过」：W2 发现自 W0 起 ci.yml 有 24 处模板表达式被写坏成 `${D} ... }}`，python-lint/python-test/alembic-check 从未真正执行，而 W0/W1 的结论写的是「CI 已绿」。workflow 文件提交前必须过两道校验：`node -e "require('js-yaml').load(...)"` 解析 + 模板表达式首尾标记配对（`grep -c '${{'` 与 `}}` 相等）。

## 7. 验收指标与「重构完成」定义

### 7.1 指标表（基线 → 目标）

| 维度 | 指标                                        | 当前基线（2026-10-24）                                                          | 目标                                                 | 采集命令                            |
| ---- | ------------------------------------------- | ------------------------------------------------------------------------------- | ---------------------------------------------------- | ----------------------------------- |
| 结构 | 最大 TS 文件行数                            | 1313（apps/web/src/pages/chat.tsx）                                             | ≤ 400（W2 起逐文件收紧；W1 只要求「改动文件 ≤400」） | wc -l 排序                          |
| 结构 | 最大 Python 文件行数                        | 1100（services/agent-orchestrator/agent_orchestrator/services/runs.py）         | ≤ 500                                                | find + wc -l                        |
| 结构 | 单目录文件数                                | apps/web/src/lib 41 个                                                          | ≤ 20（用子目录分层）                                 | find 计数                           |
| 结构 | 循环依赖                                    | 已知 1 处：graph/nodes.py:23-30 → services，services/runs.py:28 → graph（双向） | 0                                                    | import 分析脚本                     |
| 结构 | 跨层 import 违规（api→ORM、shared→service） | 未测量                                                                          | 0                                                    | 架构测试                            |
| 质量 | lint 错误数                                 | design 0 error/3 warning；common 0（固化 uv 缓存后）；其余项目未逐一实测        | 0                                                    | 各项目 lint                         |
| 质量 | typecheck 错误数                            | design 0（3.1s）；web 未实测                                                    | 0                                                    | 各项目 typecheck                    |
| 质量 | 测试用例数                                  | pytest 697 + vitest 446                                                         | 不减少；新增重构覆盖                                 | pytest --collect-only / vitest list |
| 质量 | 覆盖率                                      | 有报告无阈值（reports/、coverage/）                                             | 建立阈值：变更包行覆盖 ≥ 70%，新增代码 ≥ 80%         | 现有 pytest-cov / vitest v8         |
| 质量 | TODO/FIXME 数                               | 5                                                                               | 不增加                                               | grep                                |
| 行为 | 关键回归                                    | 全绿                                                                            | 保持全绿                                             | 门禁脚本                            |
| 行为 | 黄金 run                                    | 无自动化                                                                        | 1 条可重复 golden run                                | 新增脚本                            |
| 性能 | web 构建时长                                | 未测量（构建可跑通）                                                            | 退化 ≤ 10%                                           | time npx nx build @beauty/web       |
| 性能 | web bundle 体积                             | 单 chunk 737954B + CSS 77514B（0 处懒加载）                                     | 首屏 chunk 只降不升；路由拆包后主 chunk 显著变小     | vite build 产物统计                 |
| 性能 | 测试总时长                                  | 未测量                                                                          | 退化 ≤ 20%                                           | 门禁脚本计时                        |

### 7.2 「重构完成」的定义（单波次）

- RFC 中范围的每一项都达到验收标准；
- L3 门禁全绿，指标表已更新且无退化超阈值；
- 旧路径已删除（或列出带日期的删除任务）；
- 防回流规则已生效（架构测试/lint 规则）；
- 文档单一真源已更新，过期文档已归档；
- 复盘记录已写入本文件附录。

### 7.3 明确不算完成的情况

- 只移动文件、没有删除旧实现；
- 只加抽象层、没有替换调用方；
- 测试通过但契约快照被"顺手更新"；
- 指标变好但行为有细微变化且未记录。

## 8. 风险登记与回滚

### 8.1 高风险区（默认提高验证级别）

| 区域         | 风险                                  | 缓解                                                   | 回滚                                    |
| ------------ | ------------------------------------- | ------------------------------------------------------ | --------------------------------------- |
| alembic 迁移 | 线上缺列/数据回填失败、模型与迁移漂移 | expand-contract；每个 PR 一个迁移；alembic check       | 向前兼容（不回滚 DB），代码回滚保留新列 |
| outbox 语义  | 事件重复/丢失、事务边界被挪动         | 保持"状态变更与事件同事务"；消费方 mark_processed 幂等 | revert PR；重放 outbox                  |
| SSE 协议     | 前端断流、事件顺序变化                | 契约快照 + 断线续传测试                                | 前端向后兼容解析，服务端 flag           |
| 认证/租户    | 越权、404 存在性泄漏                  | 仓储层租户隔离测试；不改 404 契约                      | revert；安全审计                        |
| 依赖升级     | 传递依赖破坏（npm/uv）                | 单独 PR、锁文件 diff 审查                              | 恢复锁文件                              |
| 共享库抽取   | 服务启动顺序/配置差异被抹平           | 先试点 2 个服务；差异显式配置化                        | 试点服务回退到旧骨架                    |
| 并发写冲突   | 多 agent 改同一文件                   | write scopes + 串行合并                                | 以最后合并者为准，重跑门禁              |

### 8.2 回滚剧本模板（每个 PR 必填）

1. 回滚触发条件（指标/错误率/告警）；
2. 回滚动作（revert <sha> / flag off / 配置回退）；
3. 数据影响（迁移是否需要补偿、outbox 是否需要重放）；
4. 验证回滚成功的命令与观察项；
5. 回滚后如何重新推进。

### 8.3 禁止清单

- 禁止大爆炸合并（一个 PR 跨多个服务/多个目录层次）；
- 禁止在重构 PR 中混入功能/缺陷修复；
- 禁止手改生成物、锁文件、服务 .env；
- 禁止删除或弱化既有测试来"让门禁变绿"；
- 禁止在没有基线的情况下做"性能优化"。

## 9. 现状基线（2026-10-24 勘察）

### 9.1 项目清单

| 路径                        | Nx 项目               | 类型              | 技术栈                                                          | 源码行数          | 测试用例 |
| --------------------------- | --------------------- | ----------------- | --------------------------------------------------------------- | ----------------- | -------- |
| apps/web                    | @beauty/web           | React 应用        | React 19 + react-router v8 + react-query + Tailwind v4 + Vite 8 | 21860（120 文件） | 293      |
| apps/site                   | @beauty/site          | Next.js 官网      | Next.js App Router + Tailwind v4，静态导出                      | 1509（16 文件）   | 0        |
| apps/desktop                | @beauty/desktop       | Electron 桌面     | Electron 44 + React 19 + 本地运行时                             | 6734（43 文件）   | 58       |
| packages/design             | @beauty/design        | React 组件库      | 纯 CSS + bd- 前缀 + --bd-\* 令牌 + rspress                      | 3216（57 文件）   | 95       |
| packages/common             | common                | Python 共享库     | 错误模型/配置基类/日志/JWT/存储/观测                            | 1561（13 文件）   | 71       |
| packages/database           | database              | Python 共享库     | Base/mixin/session_scope/Repository/UoW                         | 400（5 文件）     | 22       |
| packages/events             | events                | Python 共享库     | 事件信封/总线/outbox/dispatcher                                 | 613（4 文件）     | 26       |
| services/agent-orchestrator | agent-orchestrator    | Python 服务 :8002 | FastAPI + LangGraph + 事件溯源 + SSE                            | 15878（70 文件）  | 386      |
| services/llm-gateway        | llm-gateway           | Python 服务 :8003 | FastAPI + litellm 唯一模型出口                                  | 671（17 文件）    | 22       |
| services/connector-service  | connector-service     | Python 服务 :8004 | FastAPI 连接器框架                                              | 589（18 文件）    | 11       |
| services/knowledge-service  | knowledge-service     | Python 服务 :8006 | FastAPI 分块/嵌入/检索                                          | 950（19 文件）    | 23       |
| services/account-service    | account-service       | Python 服务 :8001 | FastAPI 用户/组织/配额/套餐 + 自建认证                          | 3609（36 文件）   | 39       |
| services/workspace-service  | workspace-service     | Python 服务 :8005 | FastAPI 工作区/交付物/对象存储                                  | 2758（27 文件）   | 74       |
| services/bff                | bff                   | Python 服务 :8007 | FastAPI 同源 /api 门面                                          | 1160（16 文件）   | 23       |
| tools/eslint-config         | @beauty/eslint-config | 共享工具包        | ESLint 9 flat config 工厂                                       | 0                 | 0        |

合计：Python 源码约 2.82 万行、pytest 697（103 个测试文件，collect 实测）；TS/TSX 源码 33319 行（236 文件）+ CSS 2226 行，vitest 446（74 文件，web 45 / desktop 10 / design 19）。

### 9.2 依赖与构建事实

- Nx 依赖图只包含 JS 侧边：@beauty/web、@beauty/desktop、@beauty/site → @beauty/design；@beauty/design → @beauty/eslint-config。**Python 项目之间在 Nx 图中没有边**（pyproject 的 workspace 依赖不会被 @nxlv/python 自动写成 implicitDependencies），因此 nx affected 与 test.dependsOn [^build] 对 shared lib → service 的联动是缺失的，这是 W0 要补的（给 Python 项目加 implicitDependencies 或改用 nx 的 inputs 依赖）。
- 源码直连：@beauty/source 条件让 workspace 内包直接消费 src/，改组件即时生效；仓库外走 dist。
- 共享 venv：全仓一份 .venv、一份 uv.lock；uv run 会按项目 dev group 精确同步，测试依赖必须写在各自 pyproject。
- 无 CI：无 .github 目录；deploy/ 只有 docker-compose.yml、nginx.conf；当前"合并前门禁"完全依赖人工/本地脚本。

### 9.3 结构热点（按行数，重构优先级）

| 文件                                                                      | 行数 | 主要问题                                        | 波次 / 任务 | 删除条件                             |
| ------------------------------------------------------------------------- | ---- | ----------------------------------------------- | ----------- | ------------------------------------ |
| apps/web/src/pages/chat.tsx                                               | 1313 | 页面+SSE+状态+渲染混杂                          | W2 / R-17   | 拆到 features/chat/ 后原文件 ≤400 行 |
| services/agent-orchestrator/agent_orchestrator/services/runs.py           | 1100 | 服务层上帝模块（SSE/artifact/配额/状态机/事件） | W3 / R-28   | 按 5 类职责拆包后 ≤500 行            |
| apps/web/src/pages/mcp.tsx                                                | 941  | 页面+表单+API 混合                              | W2 / R-18   | 拆出表单与数据 hooks 后 ≤400 行      |
| apps/web/src/pages/chat.test.tsx                                          | 901  | 测试与实现耦合                                  | W2 / R-17   | 随实现重写为分层测试                 |
| services/agent-orchestrator/agent_orchestrator/services/mcp.py            | 792  | MCP 编排与协议混杂                              | W3 / R-29   | 编排/协议/传输分离                   |
| services/agent-orchestrator/agent_orchestrator/services/conversations.py  | 682  | 会话服务多职责                                  | W3 / R-29   | 按域拆分                             |
| services/agent-orchestrator/agent_orchestrator/services/skill_runner.py   | 666  | Skills 执行与策略混杂                           | W3 / R-28   | 执行/策略分离                        |
| apps/web/src/components/console/sidebar.tsx                               | 663  | 导航+工作区切换+状态                            | W2 / R-18   | 拆出导航数据与菜单组件后 ≤400 行     |
| services/agent-orchestrator/agent_orchestrator/services/mcp_oauth.py      | 660  | OAuth 与 MCP 耦合                               | W3 / R-29   | OAuth 独立成模块                     |
| services/agent-orchestrator/agent_orchestrator/services/skill_packages.py | 624  | 包解析与文件系统耦合                            | W3 / R-29   | 解析/存储分离                        |
| apps/web/src/pages/workspace.tsx                                          | 610  | 页面与数据获取混合                              | W2 / R-18   | 数据 hooks 与视图分离后 ≤400 行      |
| services/agent-orchestrator/agent_orchestrator/graph/nodes.py             | 601  | 图节点集中单文件                                | W3 / R-27   | 按节点分组 + 契约显式化              |
| apps/web/src/components/console/workspace-switcher.tsx                    | 543  | UI + 业务规则                                   | W2 / R-18   | 规则移入 hooks，组件 ≤400 行         |
| services/agent-orchestrator/agent_orchestrator/main.py                    | 522  | 服务骨架（lifespan 378 行 + 13 参数注入）       | W3 / R-29   | 骨架抽到 core 容器，main ≤200 行     |
| apps/web/src/lib/presentation.ts                                          | 477  | 展示层工具聚合                                  | W1 / R-13   | 按用途拆分并去重                     |
| apps/web/src/pages/settings.tsx                                           | 472  | 设置页多 tab 混杂                               | W2 / R-18   | 每个 tab 独立组件                    |

### 9.4 工程系统现状

| 维度       | 现状                                                                                                                             | 差距                                                         |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------ |
| CI         | 无 .github，无流水线                                                                                                             | 缺少 lint/typecheck/test/build、迁移检查、零网络约束的自动化 |
| 本地开发   | scripts/dev.sh 一键起 7 服务 + web，含健康检查/seed/reset                                                                        | 依赖本机环境，无容器化开发环境                               |
| 部署       | deploy/docker-compose.yml + nginx.conf                                                                                           | 无镜像构建流水线、无环境分层配置                             |
| 文档       | architecture.md 与 architecture-v2.md 并存；feature-gap.md 与 gap-2026-10 重叠；docs/tasks-m0.md、docs/tasks-m0-13.md 历史任务书 | 无单一真源、无 ADR、无归档策略                               |
| 约定文件   | 仅根 AGENTS.md（20KB）                                                                                                           | 无子树级 AGENTS.md；无 PR 模板                               |
| 版本/发布  | 无 CHANGELOG、无版本策略、无发布流程                                                                                             | 重构后无法追踪变更                                           |
| 覆盖率门禁 | 有报告（reports/、coverage/），无阈值                                                                                            | 质量只能看不能拦                                             |
| 依赖/安全  | 有锁文件；无依赖更新策略、无安全扫描                                                                                             | 供应链风险                                                   |
| 代码质量   | ESLint + Prettier + ruff + tsc strict；TODO 债极低                                                                               | 无 import 边界规则、无文件行数上限                           |

### 9.5 必须保住的资产

1. 697 个 pytest + 446 个 vitest 用例全部零网络；
2. 仓储层租户隔离与统一 404 契约；
3. 事务性 outbox（状态改了事件一定发、回滚一定不发）+ 消费幂等；
4. 事件溯源 + SSE 断线续传（meta/delta/done）；
5. 服务自建认证（旋转刷新令牌、JWKS/OIDC 发现）；
6. 本地一键开发链路（dev.sh + seed + reset）。

### 9.6 与既有 gap 分析的衔接

gap-2026-10/00-summary.md 已给出 P0/P1/P2 的**能力缺口**；本文件补的是**流程与结构**缺口。两者关系：

| gap 类别   | 例子                                     | 本流程处理方式                                     |
| ---------- | ---------------------------------------- | -------------------------------------------------- |
| 结构重构   | chat.tsx 拆分、服务骨架抽取、文档收敛    | 走完整七阶段（W1–W3）                              |
| 平台化补缺 | 最小 CI、traceId、usage 生产者、黄金 run | 走七阶段 + ADR（W0/W3/W4）                         |
| 能力缺口   | 上下文压缩、hooks、Skills DB 化、沙箱    | 不在本轮重构范围，保留 gap 文档作为需求池（P1/P2） |
| 产品功能   | Todo/Goal、文件树、模型选择              | 不走本流程，按功能开发流程排期                     |

### 9.7 门禁实测基线（2026-10-24，受限环境实测）

| 检查                      | 命令                                           | 结果   | 耗时  | 备注                                                                                 |
| ------------------------- | ---------------------------------------------- | ------ | ----- | ------------------------------------------------------------------------------------ |
| design lint               | npx nx lint @beauty/design                     | EXIT 0 | 4.0s  | 3 warning                                                                            |
| design typecheck          | npx nx typecheck @beauty/design                | EXIT 0 | 3.1s  | —                                                                                    |
| common lint（默认环境）   | npx nx lint common                             | EXIT 1 | —     | uv 缓存 $HOME/.cache/uv 不可写：Failed to initialize cache / Operation not permitted |
| common lint（固化 cache） | UV_CACHE_DIR=$PWD/.uv-cache npx nx lint common | EXIT 0 | 2s    | 受限环境必须显式设 UV_CACHE_DIR 与 UV_PYTHON_INSTALL_DIR                             |
| common test（固化 cache） | UV_CACHE_DIR=$PWD/.uv-cache npx nx test common | EXIT 0 | 18.1s | pytest 真实执行通过                                                                  |

结论：JS 侧门禁开箱可用；Python 侧门禁在受限环境/CI 中必须先固化 uv 缓存，否则所有 Python target 直接失败。这一条必须在 W0 解决，否则门禁脚本在 CI 上不可用。

## 10. 重构路线图（波次计划）

### 10.1 组织方式

全部候选重构项按依赖与风险分成 5 个波次。每个波次是一组 RFC 的执行批次：波次内可并行的任务进任务板（写明 write scope），波次结束做 L3 门禁 + 指标对比 + 复盘（第 11.5 节模板），并把流程改进写回本文件。

| 波次 | 主题                                     | 目标                                           | 出口条件                                                                                      |
| ---- | ---------------------------------------- | ---------------------------------------------- | --------------------------------------------------------------------------------------------- |
| W0   | 立门禁、立真源                           | 让后续每个改动都能被验证、被记录               | CI 骨架跑通；uv 缓存固化；文档单一真源生效；Python 依赖图正确                                 |
| W1   | 前端基座与工程约束                       | 消除前端重复传输层、建立边界规则               | 10 份 request 收敛为 1 个工厂；3 份 SSE reader 收敛为 1 个基座；路由级拆包；lint 边界规则生效 |
| W2   | 页面拆分 + 跨端共享 + service-kit 试点   | 高压文件降到阈值附近；服务骨架去重             | chat.tsx ≤ 400 行；connector/knowledge 用同一 service-kit 启动；web/desktop 共享展示层        |
| W3   | agent-orchestrator 深水区 + 质量 ratchet | 拆掉最高风险的上帝模块，同时把质量指标变成门禁 | runs.py 拆分完成、graph 无反向依赖；覆盖率阈值与零网络硬门禁生效                              |
| W4   | 发布与运维                               | 可观测、可回滚、可发布                         | CHANGELOG/release 流程；容器化部署；健康探针统一                                              |

排序理由：先门禁后重构（否则改完无法验证）；先低风险高收益后高风险（先拿前端基座与文档收敛的确定性收益）；平台类重构（service-kit、AO）放在测试与门禁收紧之后；发布/运维类最后做，因为它们依赖稳定的构建与门禁。

### 10.2 候选清单总表

规模：S = 单文件/少量文件；M = 单模块或单服务；L = 跨项目或影响契约。风险：低 = 行为等价易验证；中 = 有契约/配置耦合；高 = 运行语义或跨服务。

#### W0 立门禁、立真源

| ID   | 范围           | 重构项                                                                                                                                                   | 证据                                                                                          | 规模 | 风险 |
| ---- | -------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------- | ---- | ---- |
| R-01 | 全仓           | 固化 uv 缓存与 Python target 环境（UV_CACHE_DIR/UV_PYTHON_INSTALL_DIR），nx target 内自带默认值                                                          | 受限环境 common lint/test EXIT 1（uv 缓存不可写）；见 9.7                                     | S    | 低   |
| R-02 | 全仓           | 根 package.json 补 lint/test/format/refactor-gate 脚本；补 engines、.nvmrc                                                                               | 根 package.json 无 test/format script、无 engines                                             | S    | 低   |
| R-03 | .github        | 最小 CI：6 个 job（setup / affected-js / python-lint / python-test / lock-check / alembic-check）                                                        | 无 .github；test-ci 依赖 Nx Cloud 不可用；JS/Python test 参数不同                             | L    | 中   |
| R-04 | 全仓           | 给 Python 项目补 Nx implicitDependencies（服务→共享库），让 affected 与 [^build] 生效                                                                    | 9.2 节：Nx 图中 Python 项目无边                                                               | S    | 低   |
| R-05 | docs           | 文档单一真源：architecture.md 合并 v2 后重写为唯一现行版，v2/feature-gap/tasks-m0\* 移 docs/archive/2026-10/，新建 docs/README.md 索引与 docs/roadmap.md | 9.4 节；审计判定三处权威互斥（architecture.md 自称唯一源、v2 称 v1 仍有效、tasks-m0 依据 v2） | M    | 低   |
| R-06 | .github / docs | PR 模板 + commit 规范 + 归档检查清单                                                                                                                     | 无 PR 模板；git log 已是 Conventional Commits 实践                                            | S    | 低   |
| R-07 | 全仓           | 门禁脚本 scripts/refactor-gate.sh（第 6.2 节设计），支持单项目与 affected 两种模式                                                                       | 当前验证完全依赖人工                                                                          | M    | 低   |

#### W1 前端基座与工程约束

| ID   | 范围                | 重构项                                                                                            | 证据                                                                                                                                                                                                                         | 规模 | 风险 |
| ---- | ------------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | ---- |
| R-08 | apps/web            | 环境变量集中：14 处 import.meta.env 收敛为 src/config 单点，带默认值与校验                        | 审计：14 处 import.meta.env                                                                                                                                                                                                  | S    | 低   |
| R-09 | apps/web            | queryKey registry：散落字面量收敛为集中键工厂，失效行为显式化                                     | 审计：queryKey 全为字面量，无 registry                                                                                                                                                                                       | S    | 低   |
| R-10 | apps/web            | 路由级拆包：app.tsx 静态 import 15 页改为 lazy + Suspense + 路由骨架                              | app.tsx:1-23 静态 import；0 处 lazy/Suspense；单 chunk 737954B                                                                                                                                                               | S    | 低   |
| R-11 | apps/web            | HTTP client 工厂：10 份同构 request<T> + 手写 fetch 收敛到 http.ts 之上                           | http.ts 被 27 个文件引用（含测试）；10 份同构 request<T>：account-api:59、api-keys-api:21、conversations-api:90、insights-api:42、knowledge-api:45、mcp-api:112、outbox-api:33、runs-api:90、skills-api:70、workspace-api:47 | M    | 低   |
| R-12 | apps/web            | SSE 基座：3 份手写 reader 收敛为统一流解析 + 重连                                                 | runs-api.ts:224、notifications-api.ts:56、conversations-api.ts:318 各自 getReader + 手写帧解析；无重连/取消抽象                                                                                                              | M    | 中   |
| R-13 | apps/web            | 拆分 lib/presentation.ts（477 行），清理重复 formatTime/formatSize                                | presentation.ts 477 行；formatTime/formatSize 共 7 份定义（6 处重复）：settings.tsx:33、outbox.tsx:39、knowledge.tsx:27/34、workspace.tsx:39/46、mcp.tsx:95                                                                  | S    | 低   |
| R-14 | tools/eslint-config | 边界与体量规则：import 边界、max-lines、no-restricted-paths                                       | configs/base.js:10-34 只有 ignores；无 import 边界、无 max-lines、无 no-restricted-paths 规则                                                                                                                                | S    | 低   |
| R-15 | scripts             | 本地脚本健壮性：dev.sh 进程组清理、lsof 依赖、端口误判、首次 uv sync；reset 清 services/\*/.cache | 审计实测 4 个问题；account 签名密钥在 ./.cache                                                                                                                                                                               | M    | 低   |
| R-16 | docs / 全仓         | 文档门禁：Prettier doc 检查 + markdownlint（或等价）纳入门禁脚本                                  | 审计：Prettier 无 script/target、无 markdownlint                                                                                                                                                                             | S    | 低   |

#### W2 页面拆分 + 跨端共享 + service-kit 试点

| ID   | 范围               | 重构项                                                                                                                                                 | 证据                                                                                                                                                                                                                                                                                                                          | 规模 | 风险          |
| ---- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | ------------- |
| R-17 | apps/web           | chat.tsx（1313 行）按 features/chat/\* 拆分：数据 hooks、SSE 投影、消息渲染、输入区、审批块分离；同步重写 901 行测试                                   | chat.tsx:72-1171 单组件、8 useQuery、6 useEffect；同时耦合 conversations 与 runs 两域                                                                                                                                                                                                                                         | L    | 高            |
| R-18 | apps/web           | mcp.tsx（941）、sidebar.tsx（663）、workspace.tsx（610）、settings.tsx（472）按同法拆分                                                                | 四文件均 >470 行且职责≥3（页面/表单/数据获取/业务规则），行数见 §9.3                                                                                                                                                                                                                                                          | M    | 中            |
| R-19 | apps/web + desktop | 抽取 @beauty/console-ui：消息渲染、原语组件、布局外壳共享；desktop 保留 IPC 数据层                                                                     | desktop renderer 与 web 纯拷贝分叉（约 1300 行会话 UI + 311 行原语）                                                                                                                                                                                                                                                          | L    | 中            |
| R-20 | apps/web + site    | 清理重复：web 站点壳与 About 页、site/web 字节级重复工具（button-class.ts、lib/utils.ts）、重复 logo                                                   | 审计：md5 一致；两份 logo 已分叉                                                                                                                                                                                                                                                                                              | S    | 低            |
| R-21 | 全仓 TS            | tsconfig/vitest 收敛：3 份 tsconfig 改为 extends 根 base；vitest 配置收敛；补 site 与 console 组件盲区测试                                             | 审计：tsconfig 全量重写；vitest 4 份；site 零测试；console 21 src 仅 5 test                                                                                                                                                                                                                                                   | M    | 中            |
| R-22 | Python 服务        | service-kit：create*service_app + lifespan 钩子 + auth deps 工厂 + health 工厂 + BaseAppSettings.auth*\* + secret_box + 限流；先迁 connector/knowledge | connector 与 knowledge main.py 归一化后 82/88 行相同；health.py 共 7 份、5 种 md5（最大同组仅 2 份）；get_tenant_id/get_session 各 5 份                                                                                                                                                                                       | L    | 中            |
| R-23 | Python 测试        | service_testkit：统一 conftest、make_settings/make_client 默认钉死零网络                                                                               | conftest 重复 5 份；零网络 pin 仅 AO 完整                                                                                                                                                                                                                                                                                     | M    | 中            |
| R-24 | Python 迁移        | alembic 共享 env 模板 + 迁移 smoke test（真实 head + create_all 对齐检查）                                                                             | 五服务迁移链完整但测试零 alembic 引用                                                                                                                                                                                                                                                                                         | M    | 中            |
| R-25 | Python 安全        | 收编重复原语：Fernet ×4、SlidingWindowLimiter ×2、X-Internal-Secret 校验散落                                                                           | Fernet：account/core/byok.py:9、agent-orchestrator/services/mcp_crypto.py:9、bff/core/security.py:16、connector/core/crypto.py:11；SlidingWindowLimiter：account/core/ratelimit.py:9、bff/core/rate_limit.py:10；X-Internal-Secret：account/api/deps.py:33、account/api/v1/auth.py:125、agent-orchestrator/api/internal.py:43 | S    | 低（随 R-22） |
| R-26 | apps/design        | 主题令牌单一来源 + Dialog/Button 合一（design 与 web 两套合一）                                                                                        | web 只用 8/20 组件却自建 ui.tsx 360 行 + dialog 127 行；主题 CSS 三份                                                                                                                                                                                                                                                         | M    | 中            |

#### W3 agent-orchestrator 深水区 + 质量 ratchet

| ID   | 范围               | 重构项                                                                                                   | 证据                                                                                                                                                                                                                                                       | 规模 | 风险 |
| ---- | ------------------ | -------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---- | ---- |
| R-27 | agent-orchestrator | core/ports 契约 + graph/delta 显式注入（去掉进程级 ContextVar 单例）                                     | graph/nodes.py:23-30 → services；services/runs.py:28 → graph，双向依赖；graph/delta.py:32-40 进程级 ContextVar                                                                                                                                             | L    | 高   |
| R-28 | agent-orchestrator | 拆 services/runs.py（1100 行）：SSE 投影 / artifact / 配额 / 状态机 / 事件 5 类职责分离                  | 热点表                                                                                                                                                                                                                                                     | L    | 高   |
| R-29 | agent-orchestrator | services 按域分包；api 瘦身（去掉直连 ORM/仓储、内嵌 SSE 帧）；AppContainer 替代 app.state.\* 116 处     | api/v1/runs.py:30、notifications.py:29/90、outbox.py:63-111；main.py:81-518                                                                                                                                                                                | L    | 高   |
| R-30 | packages/common    | common 瘦身：documents 移出（仅 AO 用）、域指标拆分、重依赖隔离（避免 bff/llm-gateway 继承文档解析依赖） | common.documents 仅 agent-orchestrator/services/attachments.py:10 引用；common.metrics 混入 AO/gateway 域指标（llm.py:16、runs.py:20、jobs_worker.py:15、usage.py:19、completions.py:10）；common/pyproject.toml:16-17 的 pypdf/python-docx 被所有服务继承 | M    | 中   |
| R-31 | 全仓               | 覆盖率 ratchet：建立阈值（变更包行覆盖 ≥70%、新增代码 ≥80%），CI 与本地门禁都检查                        | 有报告无阈值                                                                                                                                                                                                                                               | M    | 中   |
| R-32 | Python 测试        | 零网络硬门禁：pytest-socket 或等价插件默认禁网，放行名单显式声明                                         | 零网络目前只是约定                                                                                                                                                                                                                                         | M    | 中   |
| R-33 | 全仓               | 黄金 run 回归脚本：可重复的端到端 run（会话→消息流→run→审批→交付物）                                     | 无自动化黄金路径                                                                                                                                                                                                                                           | M    | 中   |

#### W4 发布与运维

| ID   | 范围     | 重构项                                                                                          | 证据                                                                                                                             | 规模 | 风险 |
| ---- | -------- | ----------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------- | ---- | ---- |
| R-34 | 全仓     | 发布流程：版本策略统一、CHANGELOG 自动生成、nx release 或等价脚本                               | 无 CHANGELOG；版本 0.0.0/0.0.1/0.1.0/1.0.0 并存                                                                                  | M    | 低   |
| R-35 | 全仓     | 依赖更新与安全扫描：Dependabot 或 Renovate + npm audit/uv 安全扫描纳入 CI                       | 无更新策略、无安全扫描                                                                                                           | S    | 低   |
| R-36 | deploy   | 容器化：补 Dockerfile（当前无）、compose 补全、nginx 探针与 TLS 说明、deploy/web/README.md 修正 | deploy/docker-compose.yml 仅 pg+redis；无 Dockerfile；README 过时                                                                | L    | 中   |
| R-37 | 全仓服务 | 健康探针统一：/healthz 与 /readyz 全服务一致，nginx/BFF 暴露                                    | /readyz 仅部分服务；nginx 不暴露探针                                                                                             | M    | 低   |
| R-38 | 全仓     | 工作区卫生：文档链接检查纳入门禁、归档定期检查、生成物口径核对                                  | 无链接检查、无 markdownlint；生成物仅靠 .gitignore 覆盖（configs/base.js:10-34 同步列了 7 类缓存目录）；§12.3 归档规则无自动检查 | S    | 低   |

### 10.3 每波出口门禁

| 波次 | 必跑门禁                                                              | 指标要求                                                                                                    | 复盘重点                       |
| ---- | --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- | ------------------------------ |
| W0   | 全仓 lint + 受影响项目 typecheck/test + scripts/refactor-gate.sh 自测 | 无退化；CI 6 job 全绿（或本地等价执行记录）                                                                 | 门禁是否可被 agent 一键执行    |
| W1   | @beauty/web、@beauty/site lint/typecheck/test + build；bundle 记录    | 重复 request 包装 = 0；SSE reader 份数 = 1；改动文件 ≤400 行（chat.tsx 等高压文件留 W2）；bundle 基线已入库 | 边界规则是否拦住新违规         |
| W2   | 上述 + Python service-kit 试点服务 lint/test + 全仓 TS 测试           | chat.tsx ≤ 400；console-ui 被两端引用；connector/knowledge 启动路径一致                                     | 拆分等价性（黄金路径人工核对） |
| W3   | 全仓 lint/typecheck/test + AO 全量测试 + 迁移检查 + 黄金 run          | 最大 Python 文件 ≤ 700（W4 收到 ≤ 500，拆两步）；graph 无反向依赖；覆盖率阈值生效（10 Python + 6 TS 单元）  | 高风险拆分的回滚演练           |
| W4   | 发布候选全量门禁 + e2e 冒烟 + 回滚演练                                | 最大 Python 文件 ≤ 500；发布可重复；探针全绿                                                                | 运维可操作性                   |

每波收口时，门禁表的每一项都要在验证报告中贴出「命令 + 输出摘要」，指标口径必须可 grep 或可实跑复现（例：request<T> 定义数、getReader 调用数）。无法复现的指标视为未验证。

### 10.4 依赖与并行关系

- 关键路径：R-01/R-02/R-04 → R-03/R-07 → 后续所有波次（门禁先于重构）。
- 文档：R-05 → R-06（真源先定，模板再落）。
- 前端：R-08、R-09、R-10、R-13、R-14 可并行；R-11 → R-12；R-17 依赖 R-08/R-09/R-11/R-12 完成（否则拆分时重复代码无法复用）。
- 共享 UI：R-19 依赖 R-17/R-18（先有拆好的结构再抽包）；R-26 依赖 R-19（单一来源后再合并 Dialog/Button）。
- Python：R-22 → R-23/R-24/R-25；R-22 试点通过后再动 R-27 → R-28 → R-29；R-30 在 R-22 之后（避免同时改服务骨架与共享库）。
- 质量 ratchet：R-31/R-32 在 W3 与 AO 并行推进，但必须在 AO 合并前生效，作为护栏。
- 体积例外：R-17/R-19/R-22/R-27/R-28/R-29 天然超出「单 PR ≤400 行」。执行方式固定：先合一个只加 characterization tests / 契约快照的 PR（不改结构），再按职责逐 PR 拆分（每 PR 只搬一类职责、可独立 revert）。RFC 必须预先列出 PR 序列，否则不予立项。
- 发布：R-34～R-38 均在 W4，彼此可并行；R-36 依赖 R-37（探针先定再写容器配置）。

### 10.5 需要人工决策的事项

| 决策点                                       | 选项                                                                          | 推荐                                     | 影响                                    |
| -------------------------------------------- | ----------------------------------------------------------------------------- | ---------------------------------------- | --------------------------------------- |
| API 前缀口径                                 | A：全部统一为 /api/v1；B：保持 AO=/api/v1、其余=/v1 并在 service-kit 中参数化 | B（先参数化，W3 后再统一切换）           | R-22 的默认值、BFF 路由表、外部集成文档 |
| architecture.md 与 architecture-v2.md 的关系 | A：v2 合并进 architecture.md 后归档 v2；B：v2 改名 architecture.md、旧版归档  | A                                        | R-05 的执行方式与历史追溯               |
| desktop 与 web 的共享边界                    | A：抽 @beauty/console-ui 纯展示层；B：desktop 直接复用 web 包                 | A（数据层留 IPC，展示层共享）            | R-19 的包边界与构建配置                 |
| 覆盖率阈值起点                               | 行覆盖 70% / 新增 80%；或按包分级                                             | 先用全仓 ratchet（只降不升），再按包分级 | R-31 的门禁强度                         |

### 10.6 本轮不做（明确排除）

1. 能力缺口类：上下文压缩、hooks、Skills DB 化、子 run、长期记忆、computer use、插件市场/SDK、Stripe/SSO、Temporal/Inngest——保留在 docs/gap-\*/ 需求池，按 gap 流程立项。
2. 沙箱 provider 抽象（gap P1）：除非产品排期，本轮只做已有 sandbox 代码的整理，不做新 provider。
3. apps/desktop 的 main/preload 本地运行时重构：边界已良好，仅共享展示层。
4. packages/events 抽象扩展：当前只有 AO 一个消费者，不扩不删，等第二消费者出现再定。
5. 依赖大版本升级与结构重构混做：升级单独成 PR，按第 8 节风险流程走。
6. 大爆炸合并与跨波次合并：每个 R 编号独立 PR，独立回滚。

### 10.7 与 gap 能力缺口的关系

本路线图只处理结构与工程系统；gap-2026-10 的 P0/P1 能力项（usage 生产者、配额准入、token 预算、审批策略引擎、traceId/Sentry、多实例 job 领取、断点续跑、hooks、Skills DB 化、沙箱）走能力开发流程，不属于重构波次。两者的共同约束是第 6 节门禁：能力项合并前也必须通过 L1/L2；能力项若改变跨服务契约（事件 schema、SSE 帧、认证），必须同步更新第 9 节基线与契约快照。

## 11. 模板

> 模板原则：每个模板都要能直接粘进任务板/PR/文档，不留需要临时想的空格；模板字段与第 3–6 节一一对应。

### 11.1 重构 RFC 模板

\`\`\`md

# RFC-<三位编号>: <动词短语标题>

- 状态：draft | review | approved | done | withdrawn
- 作者 / 日期：
- 关联：任务板 <task-id>；gap 条目 <gap-xxx>；涉及项目 <nx 项目名列表>
- 影响面：代码 / 契约 / DB / 部署 / 文档（勾选）
- owner：implementer <agent/会话>；reviewer <不同 agent/会话>；verifier <agent/会话>
- write scopes：<工作区相对路径前缀列表；与其他任务重叠即串行>
- 验收阈值：<每项指标的目标值 + 判定命令，例：chat.tsx ≤400 行（wc -l）>
- PR 序列：<PR-1 只加 characterization tests/契约快照 → PR-2 搬 X → PR-3 删旧路径；每 PR 可独立 revert>
- 回滚演练：<在哪个环境、用什么命令验证 revert 或 flag off>

## 1. 问题与证据

- 现象（file:line、行数、耦合点、重复实现位置）
- 数据（当前指标值，取自第 7 节指标表）
- 触发条件（为什么现在做；不做会怎样）

## 2. 目标与非目标

- 目标（可验证的 1–3 条）
- 行为等价的定义：接口 / 事件 / DB 模式 / UI 行为 / 配置项，哪些必须不变
- 非目标（明确排除，防止范围膨胀）

## 3. 方案

- seam 位置与大小（S / M / L）
- 目标结构（目录树或模块图，标注依赖方向）
- 与既有约定的关系（引用根 AGENTS.md 中相关条目）

## 4. 迁移顺序（每步可独立合并）

| 步骤 | 改什么 | 删什么 | 验证命令 | 回滚方式 |
| ---- | ------ | ------ | -------- | -------- |
| 1    |        |        |          |          |

## 5. 契约与兼容

- HTTP / 事件 schema / DB 列 / 配置键 / 前端行为 的兼容策略（expand-contract 或快照）

## 6. 验证计划

- 自动化：新增/改写测试（列出文件名）
- 门禁：按第 6.1 节命令矩阵列出实际命令
- 人工：黄金路径检查单（列出 3–8 个用户可见行为）

## 7. 指标与验收

- 前后对比：文件行数 / 目录文件数 / 循环依赖 / 覆盖率 / 测试时长 / bundle 体积

## 8. 风险与回滚

- 按第 8.2 节模板填写

## 9. 估算与分工

- 预估任务数（agent 可并行上限）/ 人工日 / 关键路径
  \`\`\`

### 11.2 PR 模板

\`\`\`md

## 变更内容

- <一句话：改了什么结构，行为是否等价>

## 关联

- RFC-<nnn> / 任务板 <task-id> / 关闭 <gap 条目>

## 行为等价说明

- 未改变的：<接口/事件/DB/UI>
- 有意改变的：<逐条列出，并说明影响与公告方式>

## 验证

- [ ] npx nx lint <项目>
- [ ] npx nx typecheck <项目>（TS）
- [ ] npx nx test <项目> -- --run（TS）/ npx nx test <项目>（Python）
- [ ] npx nx build <项目>（公共 API）
- [ ] 迁移：uv run alembic check（若有 ORM 变更）
- [ ] 门禁脚本：scripts/refactor-gate.sh <项目>
- 测试结果：<通过数/总耗时>；新增测试：<文件列表>

## 指标

| 指标   | 变更前 | 变更后 |
| ------ | ------ | ------ |
| <指标> |        |        |

## 回滚

- 动作：revert <sha> / flag <name> off
- 数据影响：<无 / 需补偿说明>
- 验证：<命令>

## 自查

- [ ] 未混入功能/缺陷修复
- [ ] 未手改生成物/锁文件/.env
- [ ] 未弱化既有测试
- [ ] 旧路径已删除或已建带日期的删除任务
- [ ] 文档单一真源已更新
      \`\`\`

### 11.3 Reviewer 检查单

\`\`\`md

## 结构

- [ ] 依赖方向符合目标（不新增反向/跨层 import）
- [ ] 新文件职责单一，行数在阈值内（TS ≤ 400 / Python ≤ 500，测试文件可放宽）
- [ ] 无重复实现（同名同构工具/组件/请求封装）
- [ ] seam 之外没有"顺手改"

## 行为

- [ ] 测试数量未减少；新增断言覆盖拆分出的边界
- [ ] 契约快照变化已解释，且是兼容方向
- [ ] 错误路径（404/409/502/SSE 中断）仍可复现

## 约定

- [ ] 符合根 AGENTS.md（命名/引入方式/测试零网络/仓储不提交）
- [ ] 迁移与模型一致；事务边界未移动
- [ ] outbox 事件仍与状态变更同事务

## 可维护性

- [ ] 新人可在不读实现的情况下从 README/RFC 理解新结构
- [ ] 新增防回流机制（lint/import 规则/架构测试）
- [ ] 指标表已更新
      \`\`\`

### 11.4 ADR 模板

\`\`\`md

# ADR-<三位编号>: <决策标题>

- 日期 / 状态：proposed | accepted | deprecated | superseded by ADR-xxx

## 背景

（约束、现状事实、相关 RFC）

## 决策

（一句话结论 + 具体做法）

## 备选方案与理由

| 方案 | 优点 | 缺点 | 结论 |
| ---- | ---- | ---- | ---- |

## 影响

（代码结构、流程、门禁、运维、迁移成本）

## 后续

（需要跟进的 RFC/任务/触发条件）
\`\`\`

### 11.5 波次复盘模板

\`\`\`md

# Wave <n> 复盘

- 时间范围 / 参与成员 / 完成任务数

## 目标达成

| 计划项 | 结果 | 证据（指标/PR） |
| ------ | ---- | --------------- |

## 指标变化

| 指标 | 波次前 | 波次后 | 目标 | 结论 |
| ---- | ------ | ------ | ---- | ---- |

## 门禁与质量

- L1/L2 通过率、回滚次数、返工原因

## 做对了什么（可复用）

1.

## 做错了什么（教训）

1. 现象 → 根因 → 下次规则

## 流程改进（写入本文件对应章节）

1.

## 下一波调整

\`\`\`

### 11.6 命令速查（agent 直接复制）

\`\`\`sh

# 通用

npx nx show projects
npx nx show project <项目> # 查看 target/依赖
npx nx graph # 依赖图
npx nx run-many -t lint
npx nx affected -t lint,typecheck,test --base=origin/main

# TS/React

npx nx lint <项目> [-- --fix]
npx nx typecheck <项目>
npx nx test <项目> -- --run # 必须 -- --run，否则 watch 挂住
npx nx build <项目>
time npx nx build @beauty/web # 构建耗时基线

# 受限环境（CI/沙箱）先固化 uv 缓存，否则 Python target 直接失败

export UV_CACHE_DIR="$PWD/.uv-cache" UV_PYTHON_INSTALL_DIR="$PWD/.uv-python"

# Python

npx nx lint <项目>
npx nx test <项目> # uv run pytest，输出根 reports/coverage
npx nx run <项目>:sync # uv sync（不能写 npx nx sync <项目>）
npx nx run <项目>:add <pkg> [--name <dev组>]
cd services/<svc> && uv run alembic check

# 本地环境

npm run dev / dev:status / dev:seed / dev:reset
scripts/refactor-gate.sh <项目>
\`\`\`

### 11.7 基线表模板（docs/refactoring/baseline.md）

```md
# 基线（<波次/日期>）

- 采集环境：<OS / Node / Python / 是否受限（uv 缓存路径）>
- 采集人 / 时间：<agent / 会话 id / ISO 时间>
- 说明：每一行必须给出可直接复制的命令，数值必须是该命令的真实输出。

| 维度 | 指标                 | 采集命令                                                                     | 基线值                        | 目标     | 备注             |
| ---- | -------------------- | ---------------------------------------------------------------------------- | ----------------------------- | -------- | ---------------- |
| 结构 | 最大 TS 文件行数     | wc -l 全仓 ts/tsx 后排序取末行                                               |                               | ≤400     |                  |
| 结构 | 最大 Python 文件行数 | find packages services -name '_.py' -not -path '_/tests/\*' 后 wc -l 排序    |                               | ≤500     |                  |
| 质量 | pytest 用例数        | cd <项目> && uv run pytest --collect-only -q                                 | 697（103 文件）               | 不减少   |                  |
| 质量 | vitest 用例数        | npx nx test <项目> -- --run                                                  | 446（74 文件）                | 不减少   |                  |
| 行为 | 零网络               | 按 §6.3 钉住开关后跑测试                                                     | 全绿                          | 全绿     |                  |
| 发布 | 版本取值数 / 镜像数  | grep 各 package.json+pyproject 的 version 去重；docker compose config 列服务 | 1（全仓 0.1.0）               | 1        | W4 起纳入        |
| 安全 | 生产依赖漏洞数       | npm audit --omit=dev --audit-level=high；uv export + pip-audit               | 0                             | 0        | 全量漏洞另出报告 |
| 性能 | web 构建产物         | npx nx build @beauty/web 后统计 dist                                         | 单 chunk 737954B + CSS 77514B | 只降不升 |                  |
```

### 11.8 路线图模板（docs/refactoring/roadmap.md）

```md
# 重构路线图（<版本>）

- 来源：docs/refactoring-playbook.md 第 10 节
- 当前波次 / 最近更新：<W<n> / <日期>>

| ID   | 波次 | 范围 | 规模 | 风险 | 依赖 | owner | 状态    | PR  | 出口证据 |
| ---- | ---- | ---- | ---- | ---- | ---- | ----- | ------- | --- | -------- |
| R-01 | W0   | 全仓 | S    | 低   | 无   |       | pending |     |          |

## W<n> 出口检查

- [ ] 第 10.3 节门禁全绿（命令与结果记录）
- [ ] 指标对比达标（引用 baseline.md 行）
- [ ] 复盘已写入 §11.5，归档到 docs/refactoring/retro/w<n>.md
- [ ] 依赖本波的下一波任务已解除 blocked_by
```

### 11.9 验证报告模板（docs/refactoring/verification/NNN-slug.md）

```md
# 验证报告 RFC-<nnn>

- 范围 / PR 列表 / 合并 commit：<sha 列表>
- 验证人 / 时间 / 环境：<agent / 会话 / 受限环境说明>

## 门禁结果

| 层级 | 命令 | 结果      | 证据                 |
| ---- | ---- | --------- | -------------------- |
| L1   |      | PASS/FAIL | <日志尾部或 CI 链接> |

## 指标对比

| 指标 | 前（baseline 行） | 后  | RFC 阈值 | 结论 |
| ---- | ----------------- | --- | -------- | ---- |

## 行为等价证据

- 契约快照 diff：<文件 / 是否有意变化>
- 黄金路径人工核对：<会话→消息流→run→审批→交付物，逐项 PASS/FAIL>
- 新增/改写测试：<文件列表与数量>

## 异常与豁免

- <未达成的指标、原因、补偿措施与期限；无则写「无」>

## 结论

- [ ] 通过（可进入下一阶段） [ ] 不通过（原因与返工项）
- 签字：verifier <会话>；Lead <会话>
```

### 11.10 任务板条目模板

```md
- subject：<动词开头，例：拆分 chat.tsx 的数据 hooks>
- description：
  - 背景：<file:line 证据，1–3 条>
  - 验收标准：<可判定，例：chat.tsx ≤400 行；SSE 事件序列快照不变>
  - 明确不做：<防范围膨胀>
- write_scopes：<路径前缀列表，与并行任务不重叠>
- blocked_by：<任务 id 列表>
- owner / reviewer：<不同 agent>
- 完成证据：<PR 链接 + 门禁输出 + 指标对比>
```

### 11.11 单个 R 项执行 checklist（对应 §4.4 八步）

```md
- [ ] 1. RFC 已批准：owner / write scopes / 验收阈值 / PR 序列 / 回滚演练字段齐全
- [ ] 2. 基线已记录：本项涉及的指标与门禁命令已有「前」值（写入 baseline.md）
- [ ] 3. L 项前置：只加 characterization tests 或契约快照的 PR 已合并
- [ ] 4. 切片自检：单 PR ≤400 行；超出时按 §10.4 体积例外拆 PR 序列
- [ ] 5. 实现自检：门禁命令全部跑过，输出贴 PR；无无关格式化/生成物/锁文件
- [ ] 6. 独立评审：reviewer ≠ implementer，检查单见 §11.3
- [ ] 7. 指标验收：对比 RFC 阈值达标（未达标需在验证报告说明并给期限）
- [ ] 8. 收尾：旧路径删除（或建带日期任务）；防回流规则生效；基线/路线图/文档已更新
- [ ] 观察：合并后 24h 内检查错误日志、outbox failed、SSE 断流告警（有则回滚）
- [ ] 规则类改动（lint/tsconfig/CI 配置）必须用临时探针文件验证「命中」后再删除探针，避免配置写完从未生效
```

## 12. 文档治理与文档地图

### 12.1 现状问题

| 问题            | 证据                                                           | 影响                           |
| --------------- | -------------------------------------------------------------- | ------------------------------ |
| 架构文档双版本  | docs/architecture.md 27KB 与 docs/architecture-v2.md 36KB 并存 | 读者不知以哪个为准             |
| gap 分析重叠    | docs/feature-gap.md 27KB 与 docs/gap-2026-10/（7 篇）重叠      | 条目重复、状态漂移             |
| 任务书堆积      | docs/tasks-m0.md、tasks-m0-13.md 与 gap 报告的时间线交叉       | 已完成/未完成边界模糊          |
| 无 ADR          | 关键决策只散落在文档段落里                                     | 无法追溯"为什么这样选"         |
| 约定文件单一    | 仅根 AGENTS.md 20KB                                            | 子项目约定无处安放，长文难检索 |
| 无发布/变更记录 | 无 CHANGELOG、无版本策略                                       | 重构前后无法对照               |

### 12.2 目标文档地图

| 路径                                    | 角色                           | 单一真源范围               | 维护者     | 更新时机           |
| --------------------------------------- | ------------------------------ | -------------------------- | ---------- | ------------------ |
| AGENTS.md                               | 工程约定（命令/风格/禁止事项） | 全仓开发规则               | Lead       | 约定变化时         |
| docs/README.md                          | 文档索引（唯一入口）           | 哪份文档管什么             | Lead       | 文档增删时         |
| docs/architecture.md                    | 目标架构（唯一现行版本）       | 服务边界/数据流/技术选型   | Lead       | 架构变更（配 ADR） |
| docs/refactoring/rfc/NNN-\*.md          | 单个重构项的 RFC               | 单次重构的范围/验收/回滚   | 该片 owner | 立项时冻结         |
| docs/refactoring/roadmap.md             | 波次路线图（由第 10 节派生）   | 执行状态与排期             | Lead       | 每波开始/结束      |
| docs/refactoring/verification/NNN-\*.md | 验证报告                       | 单片的验证证据             | Verifier   | 单片验收时         |
| docs/local-dev.md                       | 本地开发与 troubleshooting     | 一键启动/环境变量/常见故障 | Lead       | 脚本或端口变化时   |
| docs/observability.md                   | 日志/指标/追踪约定             | 可观测性契约               | Lead       | 观测设施变化时     |
| docs/refactoring-playbook.md            | 重构流程（本文件）             | 流程/门禁/模板/指标        | Lead       | 波次复盘后         |
| docs/adr/ADR-\*.md                      | 关键决策记录                   | 每个决策一条               | 决策提出者 | 决策时             |
| docs/gap-\*/                            | 能力缺口需求池                 | 未落地的能力缺口           | Lead       | 缺口状态变化       |
| docs/<svc>/README.md                    | 服务运行/配置说明              | 单服务操作手册             | 服务 owner | 服务行为变化       |
| apps/\*/README.md                       | 应用运行说明                   | 单应用操作手册             | 应用 owner | 应用行为变化       |
| .github/PULL_REQUEST_TEMPLATE.md        | PR 模板                        | PR 必填项                  | Lead       | 流程变化           |

### 12.3 归档规则

1. 被新文档取代的旧文档移到 docs/archive/<yyyy-mm>/，文件头加一行「已归档，被 <新路径> 取代（日期）」；
2. 归档不删除内容，但不再更新，也不再作为依据；
3. 引用归档文档的地方同步改到新路径；
4. 每波结束时检查一次是否存在"双真源"。

### 12.4 本文件的演进

- 本文件只由 Lead（AI）在人工确认后修改；
- 每次波次复盘必须产出至少一条流程改进，写入对应章节并记入附录 B；
- 版本号规则：结构/门禁变化升 minor，措辞/示例变化升 patch。

## 附录

### 附录 A 术语表

| 术语         | 定义                                                                             |
| ------------ | -------------------------------------------------------------------------------- |
| seam         | 可替换的接缝：一个模块/函数/端口的边界，在此处切割对调用方影响最小               |
| S/M/L 切片   | 小（单文件内、≤1 任务）/ 中（单模块、2–5 任务）/ 大（跨项目、需 RFC 与分步合并） |
| 门禁         | 必须通过的验证集合，见第 6.4 节分级                                              |
| 行为等价     | 外部可观察行为不变：接口、事件、DB、UI、配置语义                                 |
| 防回流       | 用 lint/import 规则/架构测试阻止旧结构重新长回来                                 |
| 黄金路径     | 用户可见的核心流程（登录 → 会话 → 消息流 → run → 审批 → 交付物）                 |
| 波次（Wave） | 一组 RFC 的执行批次，每个波次结束做 L3 门禁与复盘                                |
| RFC / ADR    | 重构方案文档 / 架构决策记录                                                      |
| write scope  | 任务板上声明的文件/目录写范围，用于并行时避免冲突                                |

### 附录 B 变更记录

| 版本 | 日期       | 变更                                                                                                                                                                                                                                                                                                                                                                   |
| ---- | ---------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| v1.0 | 2026-10-24 | 首版：七阶段流程（0–6）、门禁矩阵、指标基线、现状基线与波次路线图                                                                                                                                                                                                                                                                                                      |
| v1.1 | 2026-10-24 | 独立复核后修订：测试基线改 697 用例/103 文件；统一「七阶段」；ADR 落点 docs/adr/；R-11/12/13/14/18/22/25/27/30/38 证据换为 file:line；RFC 模板补 owner/write scope/阈值/回滚演练；新增 §11.7–§11.11 模板与单 R checklist                                                                                                                                               |
| v1.2 | 2026-10-24 | W0 执行后流程改进（复盘 retro/w0.md）：用例数一律以 -- --run 实跑为准（§11.7 注记）；改共享图结构的 RFC 须预列牵连的 targetDefaults（§4.1 动作补充）；实施者遇 RFC 冲突的既有行为先 git stash 对照留证（§4.4 步骤补充）；等待实施者超 20 分钟无落盘应主动询问（§3.4 补充）                                                                                             |
| v1.3 | 2026-10-24 | W1 复盘 retro/w1.md：§10.3 末尾增「每波门禁须贴命令+输出摘要、指标可复现」；§11.11 checklist 增「规则类改动需探针验证命中」                                                                                                                                                                                                                                            |
| v1.6 | 2026-10-25 | W4 复盘 retro/w4.md：§11.7 基线模板增「发布」「安全」两个维度（版本取值数/镜像数、生产依赖漏洞数）；§6.3 增「安全门禁分阻断/非阻断两档」「豁免须带 owner+expires 并由脚本校验到期」「发布/版本/门禁类改动一律脚本断言」；§4.1 增「跨语言版本用各自生态断言，不强行合并编排」；文首增数值口径说明（现状数字为立项快照）                                                 |
| v1.5 | 2026-10-25 | W3 复盘 retro/w3.md：§10.3 体量口径拆两步（W3 ≤700 / W4 ≤500）并补 W4 行；§4.5 增「nx test 与直跑结果不同先查注入环境」；§6.3 增「观测类调用用 except Exception」与「禁网门禁的 ExceptionGroup 形态」；§6.4 增「门禁命令退出码显式采集，禁用管道取码」                                                                                                                 |
| v1.4 | 2026-10-25 | W2 复盘 retro/w2.md：§3.3 增「共享工作树禁 stash/reset/checkout 他人文件，演练只用 git apply -R --check」；§4.1 增「预算留 30% 给门禁既有债」；§4.5 基线更新为 pytest 724 / vitest 498 并增「跨平台失败先查实现语义」；§6.3 增 Node 下限对齐传递依赖、alembic check 需真 Postgres、本地 lint ≠ CI lint；§6.4 增「CI 结论须附 run 链接与 job 表 + workflow 需解析校验」 |

### 附录 C 参考

- 根 AGENTS.md（工程约定与命令）
- docs/architecture.md（目标架构）
- docs/gap-2026-10/00-summary.md（能力缺口总览）
- docs/local-dev.md（本地开发）
- 行业实践：Claude Code（CLAUDE.md/分层权限/hooks/自动压缩）、OpenAI Codex（AGENTS.md/沙箱审批/headless）、OpenHands（事件流架构）、SWE-agent 与 Aider（repo map 与结构化编辑）
