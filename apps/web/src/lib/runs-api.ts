/**
 * 运行控制台的数据层:agent-orchestrator 的 API 契约(架构 §6)。
 *
 * 环境变量(见 apps/web/.env.example):
 * - `VITE_RUNS_API_URL`:后端基址;留空走同源(Vite dev 由 vite.config.ts 代理到 :8002);
 * - 鉴权头由 `@/lib/auth` 决定(`dev` 临时租户头 / `token` / `logto` Bearer)。
 */

import { authHeaders } from '@/lib/auth';

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
  blocked_reason: string | null;
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

/** 后端统一错误契约(§11):{"code","message","details"}。 */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
  }
}

async function jsonHeaders(): Promise<Record<string, string>> {
  return { 'Content-Type': 'application/json', ...(await authHeaders()) };
}

/** 401 判定:控制台据此提示"未登录/令牌失效"。 */
export function isUnauthorized(error: unknown): boolean {
  return error instanceof ApiError && error.status === 401;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${BASE}${path}`, {
    ...init,
    headers: { ...(await jsonHeaders()), ...init?.headers },
  });
  if (!response.ok) {
    let code = 'request_failed';
    let message = `请求失败(${response.status})`;
    try {
      const body = (await response.json()) as { code?: string; message?: string };
      code = body.code ?? code;
      message = body.message ?? message;
    } catch {
      // 非 JSON 错误体:保留兜底文案
    }
    throw new ApiError(response.status, code, message);
  }
  return (await response.json()) as T;
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
  events: (runId: string) => request<RunEvent[]>(`/api/v1/runs/${runId}/events`),
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
    onEnd?: () => void;
    signal?: AbortSignal;
  },
): Promise<void> {
  const response = await fetch(`${BASE}/api/v1/runs/${runId}/events/stream`, {
    headers: await jsonHeaders(),
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
  for (;;) {
    const { done, value } = await reader.read();
    if (done) {
      return;
    }
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split('\n\n');
    buffer = frames.pop() ?? '';
    for (const frame of frames) {
      if (!frame.trim()) {
        continue;
      }
      if (frame.includes('event: stream.end')) {
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
