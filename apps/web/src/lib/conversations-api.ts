/**
 * 对话页的数据层:agent-orchestrator 的 conversation API 契约。
 *
 * 与 runs-api 同一服务(BASE 同源,Vite dev 由 vite.config.ts 代理到 :8002);
 * 对话语义:一条用户消息先由服务端**分流**——简单对话直接答(reply,不建 run),
 * 复杂任务才建 run(自动规划)或并入当前 run 的追加指令队列;
 * postMessage 的 mode 告诉页面实际发生了哪种。
 */

import { requestJson } from '@/lib/http';
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
