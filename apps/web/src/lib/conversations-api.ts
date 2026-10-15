/**
 * 对话页的数据层:agent-orchestrator 的 conversation API 契约。
 *
 * 与 runs-api 同一服务(BASE 同源,Vite dev 由 vite.config.ts 代理到 :8002);
 * 对话语义:一条用户消息按上下文要么触发新 run(自动规划),要么并入
 * 当前 run 的追加指令队列——postMessage 的返回值告诉页面发生了哪种。
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

/** 发消息的结果:run_created = 新开一跑;instruction_queued = 并入当前 run。 */
export type MessagePostResult = {
  message: ChatMessage;
  run: Run;
  mode: 'run_created' | 'instruction_queued';
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
  postMessage: (conversationId: string, content: string) =>
    request<MessagePostResult>(`/api/v1/conversations/${conversationId}/messages`, {
      method: 'POST',
      body: JSON.stringify({ content }),
    }),
};
