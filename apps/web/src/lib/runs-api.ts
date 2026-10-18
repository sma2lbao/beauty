/**
 * 运行控制台的数据层:agent-orchestrator 的 API 契约(架构 §6)。
 *
 * 环境变量(见 apps/web/.env.example):
 * - `VITE_RUNS_API_URL`:后端基址;留空走同源(Vite dev 由 vite.config.ts 代理到 :8002);
 * - 鉴权头由 `@/lib/auth` 决定(`dev` 临时租户头 / `token` / `logto` Bearer)。
 */

import { ApiError, isUnauthorized, jsonHeaders, requestJson } from '@/lib/http';

const BASE = (import.meta.env.VITE_RUNS_API_URL as string | undefined) ?? '';

export type RunStatus =
  | 'pending'
  | 'planning'
  | 'awaiting_approval'
  | 'running'
  | 'awaiting_confirmation'
  | 'blocked'
  | 'completed'
  | 'failed';

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
  /** 对话归属:由对话消息触发的 run 带;控制台直建为空。 */
  conversation_id: string | null;
  created_at: string;
};

export type RunEvent = {
  seq: number;
  type: string;
  payload: Record<string, unknown>;
  occurred_at?: string;
};

export type Plan = {
  run_id: string;
  status: RunStatus;
  plan_markdown: string;
};

export type Artifact = {
  id: string;
  run_id: string;
  kind: string;
  title: string;
  content: string;
  created_at: string;
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

function request<T>(path: string, init?: RequestInit): Promise<T> {
  return requestJson<T>(BASE, path, init);
}

export const runsApi = {
  list: () => request<Run[]>('/api/v1/runs?limit=50'),
  create: (body: {
    title: string;
    goal: string;
    acceptance_criteria: string[];
  }) =>
    request<Run>('/api/v1/runs', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  get: (runId: string) => request<Run>(`/api/v1/runs/${runId}`),
  getPlan: (runId: string) => request<Plan>(`/api/v1/runs/${runId}/plan`),
  events: (runId: string) =>
    request<RunEvent[]>(`/api/v1/runs/${runId}/events`),
  generatePlan: (runId: string) =>
    request<Plan>(`/api/v1/runs/${runId}/plan`, { method: 'POST' }),
  updatePlan: (runId: string, planMarkdown: string) =>
    request<Plan>(`/api/v1/runs/${runId}/plan`, {
      method: 'PATCH',
      body: JSON.stringify({ plan_markdown: planMarkdown }),
    }),
  approve: (runId: string) =>
    request<Run>(`/api/v1/runs/${runId}/approve`, { method: 'POST' }),
  reject: (runId: string, reason: string) =>
    request<Run>(`/api/v1/runs/${runId}/reject`, {
      method: 'POST',
      body: JSON.stringify({ reason }),
    }),
  confirm: (runId: string, action: 'approve' | 'reject', reason?: string) =>
    request<Run>(`/api/v1/runs/${runId}/confirm`, {
      method: 'POST',
      body: JSON.stringify({ action, reason }),
    }),
  /** blocked → 新 attempt 重回审批闸(计划保留,§6.2);返回更新后的计划。 */
  resume: (runId: string) =>
    request<Plan>(`/api/v1/runs/${runId}/resume`, { method: 'POST' }),
  /** failed → 另起一跑并继承目标/验收标准/已批准计划(§6.2);返回**新 run**。 */
  retry: (runId: string) =>
    request<Run>(`/api/v1/runs/${runId}/retry`, { method: 'POST' }),
  artifact: (runId: string) =>
    request<Artifact>(`/api/v1/runs/${runId}/artifact`),
  instructions: (runId: string) =>
    request<Instruction[]>(`/api/v1/runs/${runId}/instructions`),
  queueInstruction: (runId: string, content: string) =>
    request<Instruction>(`/api/v1/runs/${runId}/instructions`, {
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
  const headers = await jsonHeaders();
  if (handlers.lastEventId) {
    headers['Last-Event-ID'] = String(handlers.lastEventId);
  }
  const response = await fetch(`${BASE}/api/v1/runs/${runId}/events/stream`, {
    credentials: 'include',
    headers,
    signal: handlers.signal,
  });
  if (!response.ok || !response.body) {
    throw new ApiError(
      response.status,
      'stream_failed',
      `事件流连接失败(${response.status})`,
    );
  }
  handlers.onOpen?.();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let ended = false;
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      if (ended) {
        return;
      }
      // 没有 stream.end 就断开:当作网络中断,交给调用方重连(带 Last-Event-ID)。
      throw new ApiError(0, 'stream.interrupted', '事件流意外中断');
    }
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';
    for (const frame of frames) {
      if (!frame.trim()) {
        continue;
      }
      if (frame.includes('event: stream.end')) {
        ended = true;
        handlers.onEnd?.();
        return;
      }
      const event = parseEventFrame(frame);
      if (event) {
        handlers.onEvent(event);
      }
    }
  }
}
