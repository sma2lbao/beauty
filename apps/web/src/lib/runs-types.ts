/**
 * 运行控制台 API 契约的类型定义(从 runs-api.ts 拆出,W5 R-49)。
 *
 * 只含类型与常量枚举,无运行时逻辑;对外仍由 `@/lib/runs-api` 统一再导出,
 * 既有引用路径与导出名不变。
 */

export type RunStatus =
  | 'pending'
  | 'planning'
  | 'awaiting_approval'
  | 'running'
  | 'awaiting_confirmation'
  | 'awaiting_input'
  | 'blocked'
  | 'completed'
  | 'failed'
  | 'cancelled';

/**
 * run 血缘类型(P1-E §8):retry = 失败重试派生;subagent = 父 run 委派的子代理。
 *
 * 只描述血缘的「种类」,具体父 run 在 `parent_run_id`;retry 沿用旧版
 * 「由 run … 重试而来」文案,subagent 在看板与详情单独标出。
 */
export type RunParentKind = 'retry' | 'subagent';

export type Run = {
  id: string;
  title: string;
  goal: string;
  status: RunStatus;
  rounds: number;
  attempts: number;
  acceptance_criteria: string[];
  status_reason: string | null;
  /** 血缘:本 run 由哪个 run 派生(retry §6.2 / subagent §8);无血缘为 null。 */
  parent_run_id: string | null;
  /** 血缘类型;旧数据/旧接口可能缺省,缺省时按重试文案兜底。 */
  parent_kind?: RunParentKind | null;
  /** 子代理 run 的代理名(供父 run 详情与看板标注);非子代理为空。 */
  agent_name?: string | null;
  /** 会话归属:由会话消息触发的 run 带;控制台直建为空。 */
  conversation_id: string | null;
  /**
   * run 创建时使用的模型快照(P1-G §7):创建后不随会话模型变更,
   * 排障时要看的是「当时用了什么模型」;未指定模型(走网关默认)为 null。
   */
  model?: string | null;
  created_at: string;
};

export type RunEvent = {
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  occurred_at?: string;
};

/**
 * 一个执行步骤(架构 §6.3):来自 `GET /runs/{id}/steps` 的内存投影。
 *
 * 后端把每个 graph 节点的执行起止与结果固化成 `run.step.v1` 事件,但该事件
 * 不进用户事件流/SSE(避免噪声),所以步骤要按 run 单独拉取。字段比工具调用
 * 粗一级:node/phase/尝试次数/轮次/耗时,是排障时定位到「哪一段」的锚点。
 */
export type RunStep = {
  /** run 内事件序号,升序即步骤顺序。 */
  seq: number;
  /** 可寻址身份:`{run_id}:{attempt}:{node}:{round}`。 */
  step_id: string;
  node: string;
  phase: string;
  attempt: number;
  round: number;
  status: StepStatus;
  started_at: string;
  finished_at: string | null;
  duration_ms: number | null;
};

/** 步骤状态:运行中只在进程内的采集器里出现,落库后是终值。 */
export type StepStatus = 'running' | 'completed' | 'failed' | 'blocked';

/**
 * 过程项分类(P1-D 事件分类学,契约见 docs/design/event-taxonomy.md §4):
 * 计划/工具/文件变更/MCP 调用/检索/任务清单,以及 P1-E 的子代理委派。
 * item 是「过程项」维度,与节点步骤(step)正交,通过 `step_id` 挂到步骤上。
 */
export type RunItemKind =
  | 'plan'
  | 'tool'
  | 'file_change'
  | 'mcp_call'
  | 'search'
  | 'todo'
  | 'subagent';

/** 过程项生命周期相位(§5):started → delta → completed/failed。 */
export type RunItemPhase = 'started' | 'delta' | 'completed' | 'failed';

/** 过程项状态:started 相位投影为 running;终态细粒度含 denied(未执行)。 */
export type RunItemStatus = 'running' | 'completed' | 'failed' | 'denied';

/**
 * 一个过程项(GET /runs/{id}/items):服务端把 `run.item.v1` 按 `item_id`
 * 投影成「每项一条」(§6),字段保留后端 snake_case,与 `RunStep` 风格一致。
 *
 * `data` 是 kind 专属结构化内容:各相位按 key 浅合并,旧值先、新值后;
 * 前端一律按可选字段防御性读取,缺字段不渲染空块。
 */
export type RunItem = {
  /** 可寻址身份:`<run>:<attempt>:<kind>:<n>`,plan 为 `<run>:plan:<version>`。 */
  item_id: string;
  thread_id: string;
  /** turn 即 run(`turn_id = run.id`)。 */
  turn_id: string;
  kind: RunItemKind;
  /** 最新相位。 */
  phase: RunItemPhase;
  /** 终态细粒度;started 相位后端不带,投影为 running。 */
  status: RunItemStatus | null;
  /** 产出事件的图节点(plan/execute/tool_exec),计划编辑与 todo 快照为 null。 */
  node: string | null;
  /** 关联的 run.step.v1 步骤;拿不到时为 null。 */
  step_id: string | null;
  attempt: number;
  /** 短标题,前端直接显示(如「写入 src/a.ts」「计划(第 2 版)」)。 */
  title: string;
  /** 首条事件的 seq,排序锚点。 */
  seq: number;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  data: Record<string, unknown>;
};

/**
 * 任务清单条目(GET /runs/{id}/todos):由 todo 工具的服务端投影给出。
 * status 只有三态;清单是 run 级快照,revision 随每次写入递增。
 */
export type RunTodoItem = {
  content: string;
  status: 'pending' | 'in_progress' | 'completed';
};

/** 任务清单:旧 run / 从未调用 todo 工具时 todos 为空、counts 全 0、revision 为 0。 */
export type RunTodos = {
  todos: RunTodoItem[];
  counts: { pending: number; in_progress: number; completed: number };
  revision: number;
};

/** 用量加总(不含 run_id/模型拆分):子代理清单与过程项 `data.usage` 都用这一形状。 */
export type RunUsageTotals = {
  calls: number;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
};

/** 按模型拆分的用量行(模型名来自网关回包)。 */
export type RunUsageModel = RunUsageTotals & { model: string };

/**
 * `GET /runs/{id}/usage`:该 run **自身**的用量(子代理用量在各自
 * `Subagent.usage`);`by_model` 只在这个聚合接口上有。
 */
export type RunUsage = RunUsageTotals & {
  run_id: string;
  by_model: RunUsageModel[];
};

/**
 * 一个子代理(P1-E §8):父 run 委派出来的**独立 run**。
 *
 * 子代理有自己的一条 run 记录与事件流,详情沿用 `/runs/{id}` 同一套接口
 * (打开子 run = 切换到它的 runId);这里只带列表/汇总需要的摘要字段。
 * `summary` 是子代理收尾时的结论(可空),按纯文本/Markdown 展示。
 */
export type Subagent = {
  run_id: string;
  title: string;
  agent_name: string | null;
  status: RunStatus;
  status_reason: string | null;
  parent_run_id: string | null;
  created_at: string;
  usage: RunUsageTotals;
  summary: string | null;
};

export type Plan = {
  run_id: string;
  status: RunStatus;
  plan_markdown: string;
};

/** 一个 run 的交付物元数据(多件:报告 + 真实文件)。 */
export type ArtifactMeta = {
  id: string;
  run_id: string;
  kind: string;
  title: string;
  /** 后端给出的落盘文件名(含扩展名);旧数据可能为空。 */
  filename?: string | null;
  /** 后端给出的 MIME;旧数据可能为空。 */
  content_type?: string | null;
  size_bytes?: number;
  storage?: string;
  /** 产物来源(如 report/tool/output);用于展示来源,缺省不影响渲染。 */
  source?: string;
  created_at: string;
};

/** 旧 `GET /runs/{id}/artifact`(主文本交付物)仍附带正文,保留兼容。 */
export type Artifact = ArtifactMeta & { content: string };

/**
 * ask_question 的挂起提问(§10.4):来自 `run.question.required.v1` 事件载荷。
 *
 * 后端 v1 不建问题表,载荷只在事件里;字段全部防御性解析(旧事件可能缺省),
 * 问题/选项一律当纯文本渲染,不解析 markdown/HTML。
 */
export type RunQuestionOption = {
  label: string;
  description?: string | null;
};

export type RunQuestionItem = {
  header?: string | null;
  question: string;
  options: RunQuestionOption[];
  multi_select?: boolean;
};

export type RunQuestion = {
  question_id: string;
  questions: RunQuestionItem[];
  allow_custom?: boolean;
  expires_at?: string | null;
};

/** 用户对一道问题的作答:选中的选项 label + 自定义文本(二选一或并存)。 */
export type RunAnswer = {
  index: number;
  labels: string[];
  custom: string | null;
};

/** 回答/跳过挂起提问的请求体(§10.5)。 */
export type RunAnswerPayload = {
  question_id: string;
  action: 'answer' | 'decline';
  answers: RunAnswer[];
};

/** 回答接口返回:202 接受后回更新后的 run(重新进入 running)。 */
export type RunAnswerAccepted = {
  run: Run;
  accepted: boolean;
};

/** 运行中追加指令(§6.5):queued 表示等下一个边界消费。 */
export type Instruction = {
  id: string;
  run_id: string;
  content: string;
  status: 'queued' | 'consumed';
  created_at: string;
};
