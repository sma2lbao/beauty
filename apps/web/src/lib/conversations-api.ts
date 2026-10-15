/**
 * 对话页的数据层:agent-orchestrator 的 conversation API 契约。
 *
 * 与 runs-api 同一服务(BASE 同源,Vite dev 由 vite.config.ts 代理到 :8002);
 * 对话语义:一条用户消息先由服务端**分流**——简单对话直接答(reply,不建 run),
 * 复杂任务才建 run(自动规划)或并入当前 run 的追加指令队列;
 * postMessage 的 mode 告诉页面实际发生了哪种。
 */

import { ApiError, jsonHeaders, requestJson } from '@/lib/http';
import type { Run } from '@/lib/runs-api';

const BASE = (import.meta.env.VITE_RUNS_API_URL as string | undefined) ?? '';

export type Conversation = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  /** 仅详情返回:当前进行中的 run(接 SSE 用);列表项不带该字段 */
  active_run_id?: string | null;
};

export type ChatMessage = {
  id: string;
  conversation_id: string;
  run_id: string | null;
  role: 'user' | 'assistant';
  content: string;
  created_at: string;
};

/** 发送方式:auto = 服务端自动分流(默认);chat = 直接对话;task = 作为任务。 */
export type SendMode = 'auto' | 'chat' | 'task';

/**
 * 发消息的结果:
 * - chat:reply 是直接回复,run 为空(没建任务);
 * - run_created:run 是新任务;
 * - instruction_queued:run 是并入的进行中任务。
 */
export type MessagePostResult = {
  message: ChatMessage;
  run: Run | null;
  reply: ChatMessage | null;
  mode: 'chat' | 'run_created' | 'instruction_queued';
};

function request<T>(path: string, init?: RequestInit): Promise<T> {
  return requestJson<T>(BASE, path, init);
}

export const conversationsApi = {
  list: () => request<Conversation[]>('/api/v1/conversations?limit=50'),
  create: () =>
    request<Conversation>('/api/v1/conversations', {
      method: 'POST',
      body: JSON.stringify({}),
    }),
  get: (conversationId: string) =>
    request<Conversation>(`/api/v1/conversations/${conversationId}`),
  messages: (conversationId: string) =>
    request<ChatMessage[]>(`/api/v1/conversations/${conversationId}/messages`),
  postMessage: (conversationId: string, content: string, mode: SendMode = 'auto') =>
    request<MessagePostResult>(`/api/v1/conversations/${conversationId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content, mode }),
    }),
};

/**
 * 流式发消息的事件回调(SSE):meta(分流结果)→ delta(增量)→ done;失败走 onError。
 * 事件名由后端标注,前端按名分发——不猜帧内容。
 */
export type StreamMessageHandlers = {
  onMeta?: (meta: {
    mode: MessagePostResult['mode'];
    message: ChatMessage;
    run: Run | null;
  }) => void;
  onDelta?: (text: string) => void;
  onDone?: (result: {
    mode: MessagePostResult['mode'];
    reply: ChatMessage | null;
  }) => void;
  onError?: (error: { code: string; message: string }) => void;
  signal?: AbortSignal;
};

/** 解析一个具名 SSE 帧:`event: 名字` + `data: JSON`。 */
export function parseSseFrame(
  frame: string,
): [string, Record<string, unknown>] | null {
  let name: string | null = null;
  const dataLines: string[] = [];
  for (const line of frame.split('\n')) {
    if (line.startsWith('event: ')) {
      name = line.slice('event: '.length).trim();
    } else if (line.startsWith('data: ')) {
      dataLines.push(line.slice('data: '.length));
    }
  }
  if (!name || dataLines.length === 0) {
    return null;
  }
  try {
    return [name, JSON.parse(dataLines.join('\n')) as Record<string, unknown>];
  } catch {
    return null;
  }
}

/**
 * 流式发消息:模型边产出边回调 onDelta,页面据此增量渲染。
 *
 * 开流前的错误(404/409/422)仍是统一错误契约,直接抛 ApiError;
 * 开流后的失败由后端发 error 帧,走 onError(HTTP 已是 200,改不了)。
 */
export async function streamMessage(
  conversationId: string,
  content: string,
  mode: SendMode,
  handlers: StreamMessageHandlers,
): Promise<void> {
  const response = await fetch(
    `${BASE}/api/v1/conversations/${conversationId}/messages/stream`,
    {
      method: 'POST',
      headers: { ...(await jsonHeaders()), Accept: 'text/event-stream' },
      body: JSON.stringify({ content, mode }),
      signal: handlers.signal,
    },
  );
  if (!response.ok) {
    let code = 'stream_failed';
    let message = `发送失败(${response.status})`;
    try {
      const body = (await response.json()) as { code?: string; message?: string };
      code = body.code ?? code;
      message = body.message ?? message;
    } catch {
      // 非 JSON 错误体:保留兜底文案
    }
    throw new ApiError(response.status, code, message);
  }
  if (!response.body) {
    throw new ApiError(response.status, 'stream_failed', '响应没有流式内容');
  }
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
      const parsed = parseSseFrame(frame);
      if (!parsed) {
        continue;
      }
      const [name, data] = parsed;
      if (name === 'meta') {
        handlers.onMeta?.(data as Parameters<NonNullable<StreamMessageHandlers['onMeta']>>[0]);
      } else if (name === 'delta') {
        handlers.onDelta?.(typeof data.text === 'string' ? data.text : '');
      } else if (name === 'done') {
        handlers.onDone?.(data as Parameters<NonNullable<StreamMessageHandlers['onDone']>>[0]);
      } else if (name === 'error') {
        handlers.onError?.({
          code: typeof data.code === 'string' ? data.code : 'llm.upstream_error',
          message: typeof data.message === 'string' ? data.message : '模型调用失败',
        });
      }
    }
  }
}
