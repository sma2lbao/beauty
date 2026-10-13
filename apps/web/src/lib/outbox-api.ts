/**
 * 事件队列(outbox)运维页的数据层:agent-orchestrator 的 /outbox 契约(§6.3)。
 *
 * 与运行控制台同源(Vite dev 的 `/api` 代理到 :8002),所以复用同一套鉴权头。
 * 未启用 outbox 时后端返回 409 `outbox.disabled`——页面据此给出明确说明,
 * 而不是把"没开"显示成"没有事件"。
 */

import { requestJson } from '@/lib/http';

const BASE = (import.meta.env.VITE_RUNS_API_URL as string | undefined) ?? '';

export type OutboxStatus = 'pending' | 'dispatched' | 'failed';

export type OutboxEvent = {
  id: string;
  event_type: string;
  status: OutboxStatus;
  attempts: number;
  payload: Record<string, unknown>;
  available_at: string;
  dispatched_at: string | null;
  last_error: string | null;
  created_at: string;
};

export type OutboxStats = {
  pending: number;
  dispatched: number;
  failed: number;
};

function request<T>(path: string, init?: RequestInit): Promise<T> {
  return requestJson<T>(BASE, `/api/v1/outbox${path}`, init);
}

export const outboxApi = {
  stats: () => request<OutboxStats>('/stats'),
  events: (status?: OutboxStatus, limit = 50) => {
    const query = new URLSearchParams({ limit: String(limit) });
    if (status) {
      query.set('status', status);
    }
    return request<OutboxEvent[]>(`/events?${query.toString()}`);
  },
  retry: (eventId: string) =>
    request<OutboxEvent>(`/events/${eventId}/retry`, { method: 'POST' }),
  retryFailed: () =>
    request<{ requeued: number }>('/retry-failed', { method: 'POST' }),
};
