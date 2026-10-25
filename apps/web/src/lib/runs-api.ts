/**
 * 运行控制台的数据层:agent-orchestrator 的 API 契约(架构 §6)。
 *
 * 环境变量(见 apps/web/.env.example):
 * - `VITE_RUNS_API_URL`:后端基址;留空走同源(Vite dev 由 vite.config.ts 代理到 :8002);
 * - 鉴权头由 `@/lib/auth` 决定(`dev` 临时租户头 / `token` / `logto` Bearer)。
 */

import { ApiError, createHttpClient, isUnauthorized } from '@/lib/http';
import { connectSse } from '@/lib/sse';
import { runsBase as BASE } from '@/config/env';

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

export type Run = {
  id: string;
  title: string;
  goal: string;
  status: RunStatus;
  rounds: number;
  attempts: number;
  acceptance_criteria: string[];
  status_reason: string | null;
  /** 重试血缘:本 run 由哪个 failed run 派生(§6.2)。 */
  parent_run_id: string | null;
  /** 会话归属:由会话消息触发的 run 带;控制台直建为空。 */
  conversation_id: string | null;
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

/** 统一错误契约与请求封装见 @/lib/http;这里保留同名再导出,老的引用不用改。 */
export { ApiError, isUnauthorized };

const http = createHttpClient(BASE);

export const runsApi = {
  list: () => http.request<Run[]>('/api/v1/runs?limit=50'),
  create: (body: {
    title: string;
    goal: string;
    acceptance_criteria: string[];
  }) =>
    http.request<Run>('/api/v1/runs', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  get: (runId: string) => http.request<Run>(`/api/v1/runs/${runId}`),
  getPlan: (runId: string) => http.request<Plan>(`/api/v1/runs/${runId}/plan`),
  events: (runId: string) =>
    http.request<RunEvent[]>(`/api/v1/runs/${runId}/events`),
  /** 节点级步骤投影:独立于事件流(表内 seq 有空洞是正常的,别按连续 id 判断)。 */
  steps: (runId: string) =>
    http.request<RunStep[]>(`/api/v1/runs/${runId}/steps`),
  generatePlan: (runId: string) =>
    http.request<Plan>(`/api/v1/runs/${runId}/plan`, { method: 'POST' }),
  updatePlan: (runId: string, planMarkdown: string) =>
    http.request<Plan>(`/api/v1/runs/${runId}/plan`, {
      method: 'PATCH',
      body: JSON.stringify({ plan_markdown: planMarkdown }),
    }),
  approve: (runId: string) =>
    http.request<Run>(`/api/v1/runs/${runId}/approve`, { method: 'POST' }),
  reject: (runId: string, reason: string) =>
    http.request<Run>(`/api/v1/runs/${runId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
  confirm: (runId: string, action: 'approve' | 'reject', reason?: string) =>
    http.request<Run>(`/api/v1/runs/${runId}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ action, reason }),
    }),
  /**
   * 回答/跳过挂起提问(ask_question,§10.5):202 返回更新后的 run。
   *
   * 409 的 `run.question_mismatch` / `run.question_expired` /
   * `run.invalid_state` 由展示层翻译成用户能读懂的提示;重复提交幂等。
   */
  answerRunQuestion: (runId: string, body: RunAnswerPayload) =>
    http.request<RunAnswerAccepted>(`/api/v1/runs/${runId}/answer`, {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  /**
   * 协作式取消:落 cancelled 终态并撤掉排队中的 job;执行中的图不会被强杀,
   * 结果在下一个执行边界被丢弃。终态(completed/failed/cancelled)调用回 409。
   */
  cancel: (runId: string, reason?: string) =>
    http.request<Run>(`/api/v1/runs/${runId}/cancel`, {
      method: 'POST',
      body: JSON.stringify(reason ? { reason } : {}),
    }),
  /** blocked → 新 attempt 重回审批闸(计划保留,§6.2);返回更新后的计划。 */
  resume: (runId: string) =>
    http.request<Plan>(`/api/v1/runs/${runId}/resume`, { method: 'POST' }),
  /** failed → 另起一跑并继承目标/验收标准/已批准计划(§6.2);返回**新 run**。 */
  retry: (runId: string) =>
    http.request<Run>(`/api/v1/runs/${runId}/retry`, { method: 'POST' }),
  /** 旧端点:主文本交付物(带 content),仅为兼容保留。 */
  artifact: (runId: string) =>
    http.request<Artifact>(`/api/v1/runs/${runId}/artifact`),
  /** 交付物列表:报告 + 真实文件(截图/文档等),只有元数据。 */
  artifacts: (runId: string) =>
    http.request<ArtifactMeta[]>(`/api/v1/runs/${runId}/artifacts`),
  /** 下载交付物二进制:返回 Blob,文件名/MIME 由列表元数据决定。 */
  downloadArtifact: (runId: string, artifactId: string): Promise<Blob> =>
    http.blob(`/api/v1/runs/${runId}/artifacts/${artifactId}/download`),
  instructions: (runId: string) =>
    http.request<Instruction[]>(`/api/v1/runs/${runId}/instructions`),
  queueInstruction: (runId: string, content: string) =>
    http.request<Instruction>(`/api/v1/runs/${runId}/instructions`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),
};

/**
 * 解析一个 SSE 帧。后端把 `RunEventRead` JSON 放进 data 行,
 * 所以事件本身自带 seq/type;`stream.end` 帧没有 seq,返回 null。
 */
export function parseEventFrame(frame: string): RunEvent | null {
  const data = frame
    .split('\n')
    .filter((line) => line.startsWith('data: '))
    .map((line) => line.slice(6))
    .join('\n');
  if (!data) {
    return null;
  }
  try {
    const parsed = JSON.parse(data) as Partial<RunEvent>;
    if (typeof parsed.seq !== 'number' || typeof parsed.type !== 'string') {
      return null;
    }
    return {
      seq: parsed.seq,
      type: parsed.type,
      payload: parsed.payload ?? {},
      occurred_at: parsed.occurred_at,
    };
  } catch {
    return null;
  }
}

/** 按 seq 去重并升序合并(SSE 可能重放,断线重连会从游标续传)。 */
export function mergeEvents(
  current: readonly RunEvent[],
  incoming: RunEvent,
): RunEvent[] {
  if (current.some((event) => event.seq === incoming.seq)) {
    return [...current];
  }
  return [...current, incoming].sort((left, right) => left.seq - right.seq);
}

/** 订阅 run 的 SSE 事件流;`signal` 中断即断开连接。 */
export async function streamRunEvents(
  runId: string,
  handlers: {
    onEvent: (event: RunEvent) => void;
    onOpen?: () => void;
    /** 服务端正常结束(stream.end);网络异常会抛错,不会走这里。 */
    onEnd?: () => void;
    /** 断线重连游标:上一次收到的最大 seq,作为 Last-Event-ID。 */
    lastEventId?: number | null;
    signal?: AbortSignal;
  },
): Promise<void> {
  await connectSse(`${BASE}/api/v1/runs/${runId}/events/stream`, {
    lastEventId: handlers.lastEventId,
    signal: handlers.signal,
    onOpen: handlers.onOpen,
    mapError: (response) =>
      new ApiError(
        response.status,
        'stream_failed',
        `事件流连接失败(${response.status})`,
      ),
    onFrame: (frame) => {
      const event = parseEventFrame(frame.raw);
      if (event) {
        handlers.onEvent(event);
      }
    },
    onEnd: handlers.onEnd,
    // 没有 stream.end 就断开:当作网络中断,交给调用方重连(带 Last-Event-ID)。
    onInterrupted: () => {
      throw new ApiError(0, 'stream.interrupted', '事件流意外中断');
    },
  });
}
